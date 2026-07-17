import os
import sys
import time
import json
import sqlite3
import difflib
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, event
from llama_index.core import SQLDatabase, Settings, PromptTemplate
from llama_index.core.embeddings import MockEmbedding
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.core.query_engine import NLSQLTableQueryEngine

load_dotenv()

def sqlite_authorizer(action, arg1, arg2, dbname, source):
    if action == sqlite3.SQLITE_READ:
        table_name = arg1
        if table_name in ["trainers", "feedback"] and (not source or source.strip() == ""):
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK

def safe_llm_complete(prompt, retries=3, delay=5):
    for i in range(retries):
        try:
            return Settings.llm.complete(prompt)
        except Exception as e:
            if "ResourceExhausted" in str(e) or "429" in str(e):
                if i < retries - 1:
                    print(f"\033[93mRate limit reached. Retrying in {delay} seconds...\033[0m")
                    time.sleep(delay)
                    continue
            raise e

def parse_json_response(text):
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text.strip())

def main():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("\033[91mError: GEMINI_API_KEY environment variable is not set.\033[0m")
        print("Please set your Gemini API key in the .env file or environment.")
        sys.exit(1)

    db_file = "profice.db"
    if not os.path.exists(db_file):
        print(f"\033[91mError: Database file '{db_file}' not found in the current directory.\033[0m")
        sys.exit(1)

    print("\n\033[96mSelect your access role:\033[0m")
    print("1. Admin")
    print("2. Trainer")
    while True:
        choice = input("Enter choice (1 or 2): ").strip()
        if choice == "1":
            role = "admin"
            trainer_id = None
            matched_name = None
            print("\033[92mAuthenticated as Admin.\033[0m")
            break
        elif choice == "2":
            role = "trainer"
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM trainers")
            trainers = cursor.fetchall()
            conn.close()
            
            trainer_name = input("Enter your Trainer Name (e.g. Akash K): ").strip()
            if not trainer_name:
                continue
                
            exact_matches = [t for t in trainers if t[1].strip().lower() == trainer_name.lower()]
            if len(exact_matches) == 1:
                trainer_id, matched_name = exact_matches[0]
                print(f"\033[92mAuthenticated as Trainer: {matched_name} (ID: {trainer_id})\033[0m")
                break

            sub_matches = [t for t in trainers if trainer_name.lower() in t[1].lower()]
            if len(sub_matches) > 1:
                print("\033[91mError: Multiple names found, please add initial or full name.\033[0m\n")
                continue
            elif len(sub_matches) == 1:
                trainer_id, matched_name = sub_matches[0]
                print(f"\033[92mAuthenticated as Trainer: {matched_name} (ID: {trainer_id})\033[0m")
                break

            t_names = [t[1] for t in trainers]
            close_matches = difflib.get_close_matches(trainer_name, t_names, n=3, cutoff=0.5)
            if len(close_matches) > 1:
                print("\033[91mError: Multiple names found, please add initial or full name.\033[0m\n")
                continue
            elif len(close_matches) == 1:
                matched_name = close_matches[0]
                trainer_id = [t[0] for t in trainers if t[1] == matched_name][0]
                print(f"\033[93mFuzzy matched input to: {matched_name}\033[0m")
                print(f"\033[92mAuthenticated as Trainer: {matched_name} (ID: {trainer_id})\033[0m")
                break
            else:
                print("\033[91mError: Trainer name not recognized. Please try again.\033[0m\n")
        else:
            print("Invalid choice. Please enter 1 or 2.")

    print("\033[96mInitializing SQL Chatbot...\033[0m")
    
    try:
        llm = GoogleGenAI(model="models/gemini-3.1-flash-lite", api_key=api_key)
        Settings.llm = llm
        Settings.embed_model = MockEmbedding(embed_dim=768)
    except Exception as e:
        print(f"\033[91mError initializing Gemini LLM: {e}\033[0m")
        sys.exit(1)
        
    try:
        engine = create_engine(f"sqlite:///file:{db_file}?mode=ro&uri=true")
        @event.listens_for(engine, "connect")
        def set_sqlite_authorizer(dbapi_connection, connection_record):
            dbapi_connection.set_authorizer(sqlite_authorizer)

        with engine.begin() as conn:
            conn.execute(text("DROP VIEW IF EXISTS temp.v_trainers"))
            conn.execute(text("DROP VIEW IF EXISTS temp.v_feedback"))
            if role == "admin":
                conn.execute(text("CREATE TEMP VIEW v_trainers AS SELECT id, name, department, attendance FROM trainers"))
                conn.execute(text("CREATE TEMP VIEW v_feedback AS SELECT id, trainer_id, student_name, feedback_text, rating, created_at FROM feedback"))
            else:
                conn.execute(text(f"CREATE TEMP VIEW v_trainers AS SELECT id, name, department, attendance FROM trainers WHERE id = {trainer_id}"))
                conn.execute(text(f"CREATE TEMP VIEW v_feedback AS SELECT id, trainer_id, feedback_text, rating, created_at FROM feedback WHERE trainer_id = {trainer_id}"))
        
        sql_database = SQLDatabase(
            engine,
            schema="temp",
            include_tables=["v_trainers", "v_feedback"],
            view_support=True
        )
    except Exception as e:
        print(f"\033[91mError connecting to database: {e}\033[0m")
        sys.exit(1)

    import datetime
    
    if role == "admin":
        try:
            query_engine = NLSQLTableQueryEngine(
                sql_database=sql_database,
                tables=["v_trainers", "v_feedback"],
                verbose=False
            )
            
            custom_text_to_sql_tmpl = (
                "Given an input question, first create a syntactically correct {dialect} query to run, "
                "then look at the results of the query and return the answer. You can order the results "
                "by a relevant column to return the most interesting examples in the database.\n\n"
                "Never query for all the columns from a specific table, only ask for a few relevant columns "
                "given the question.\n\n"
                "Pay attention to use only the column names that you can see in the schema description. "
                "Be careful to not query for columns that do not exist. Pay attention to which column is "
                "in which table. Also, qualify column names with the table name when needed.\n\n"
                "Guidelines:\n"
                "- When filtering by text columns (like names or words), if the search term in the question "
                "is incomplete or could be a partial match, always use the LIKE operator with wildcards "
                "(e.g., column_name LIKE '%search_term%') instead of direct equality (=).\n"
                "- Correct any obvious spelling errors or typos in search terms (e.g., 'aksh' to 'Akash') "
                "to match the context of the database tables before writing the SQL query.\n"
                "- If the question asks for information or metrics that are not stored in any of the tables "
                "(such as salary, age, location, or schedule), do not guess or write a filter on unrelated columns like name. "
                "Instead, write a query that returns a constant string explaining the limitation "
                "(e.g., SELECT 'unsupported_query_salary_not_in_database') so the final answer can explain "
                "the database doesn't store this info.\n\n"
                "You are required to use the following format, each taking one line:\n\n"
                "Question: Question here\n"
                "SQLQuery: SQL Query to run\n"
                "SQLResult: Result of the SQLQuery\n"
                "Answer: Final answer here\n\n"
                "Only use tables listed below.\n"
                "{schema}\n\n"
                "Question: {query_str}\n"
                "SQLQuery: "
            )
            custom_prompt = PromptTemplate(custom_text_to_sql_tmpl)
            query_engine.update_prompts({"sql_retriever:text_to_sql_prompt": custom_prompt})
            
            # Load Admin 3-month context data
            cutoff_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%Y-%m-%d')
            with engine.connect() as conn:
                trainers_all = conn.execute(text("SELECT id, name, department, attendance FROM v_trainers")).fetchall()
                feedback_recent = conn.execute(text(f"SELECT id, trainer_id, student_name, feedback_text, rating, created_at FROM v_feedback WHERE created_at >= '{cutoff_date}'")).fetchall()
            
            trainers_table = "\n".join([f"| {t[0]} | {t[1]} | {t[2]} | {t[3]}% |" for t in trainers_all])
            feedback_table = "\n".join([f"| {f[5]} | {f[0]} | {f[1]} | {f[2]} | {f[3]} | {f[4]} |" for f in feedback_recent])
            
            admin_context = (
                f"You are the SQL Database Assistant for the Admin.\n"
                f"You have access to the database data for the LAST 3 MONTHS (since {cutoff_date}).\n\n"
                f"Current Date: {datetime.date.today().strftime('%Y-%m-%d')}\n\n"
                f"Trainers Profile (v_trainers):\n"
                f"| ID | Name | Department | Attendance |\n"
                f"|---|---|---|---|\n"
                f"{trainers_table}\n\n"
                f"Recent Feedback Received (v_feedback - Last 3 Months):\n"
                f"| Date | Feedback ID | Trainer ID | Student Name | Feedback Comment | Rating |\n"
                f"|---|---|---|---|---|---|\n"
                f"{feedback_table if feedback_table else '| - | - | - | - | - | - |'}\n\n"
                f"Instructions:\n"
                f"1. If the user asks a question about the last 3 months, profile info, or general conversational questions, answer it directly from the context.\n"
                f"2. Keep your answers concise, clear, and direct. Avoid flowery praise, moral support, or long-winded comments.\n"
                f"3. If the user asks for historical data beyond 3 months (prior to {cutoff_date}) or requires aggregate SQL computations over the entire database tables, respond EXACTLY in this format:\n"
                f"   NEED_SQL: <original question rewritten as a clean standalone database query>\n"
                f"   Do not add any other text before or after this string."
            )
            
        except Exception as e:
            print(f"\033[91mError initializing Query Engine / Context: {e}\033[0m")
            sys.exit(1)
    else:
        # Load Trainer data for context-based LLM chatbot
        try:
            with engine.connect() as conn:
                trainer_data = conn.execute(text("SELECT name, department, attendance FROM v_trainers")).fetchone()
                feedback_data = conn.execute(text("SELECT feedback_text, rating FROM v_feedback")).fetchall()
            
            if not trainer_data:
                print("\033[91mError: Could not retrieve trainer profile details.\033[0m")
                sys.exit(1)
                
            trainer_name, department, attendance = trainer_data
            feedback_list = "\n".join([f"- Rating: {f[1]} | Comment: \"{f[0]}\"" for f in feedback_data])
            
            trainer_context = (
                f"You are a helpful SQL database assistant for a trainer named {trainer_name} (Department: {department}, Attendance: {attendance}%).\n"
                f"You have access to their profile and their feedback records (which are anonymized for student privacy):\n\n"
                f"Trainer Profile:\n"
                f"- Name: {trainer_name}\n"
                f"- Department: {department}\n"
                f"- Attendance: {attendance}%\n\n"
                f"Feedback Received:\n"
                f"{feedback_list if feedback_list else '- No feedback received yet.'}\n\n"
                f"Your task is to answer user questions using this data. Answer general conversational questions (chitchat) and database-related queries directly, in a friendly and personalized manner.\n"
                f"Always address the user directly as 'you' or '{trainer_name}' where appropriate (e.g. say 'Your average rating is...' or 'You received a feedback comment stating...').\n\n"
                f"Critical Guidelines for Response Style:\n"
                f"- Keep responses brief, direct, and focused on the database facts. Avoid unnecessary filler words.\n"
                f"- DO NOT write excessive moral support, flowery praise, or long-winded encouragement (e.g., do NOT say things like 'It is completely normal to have constructive feedback' or 'every great educator uses those insights to refine their craft').\n"
                f"- Keep expressions of praise simple (e.g., 'Good job' or 'You are on the right path') and immediately follow up with the requested data or specific feedback comments."
            )
        except Exception as e:
            print(f"\033[91mError loading trainer data: {e}\033[0m")
            sys.exit(1)

    chat_history = []

    print("\033[92mChatbot initialized successfully!\033[0m")
    print(f"Database: \033[93mprofice.db\033[0m (Role: \033[95m{role}\033[0m, Views: v_trainers, v_feedback)")
    print("Model: \033[93mgemini-3.1-flash-lite\033[0m")
    print("Type \033[95m'exit'\033[0m or \033[95m'quit'\033[0m to end the conversation.\n")

    while True:
        try:
            question = input("\033[94mAsk a question about the database:\033[0m\n> ")
            if not question.strip():
                continue
            if question.strip().lower() in ["exit", "quit"]:
                print("\033[96mGoodbye!\033[0m")
                break
            
            if role == "admin":
                history_str = ""
                for prev_q, prev_a in chat_history[-5:]:
                    history_str += f"User: {prev_q}\nAssistant: {prev_a}\n"
                
                chat_prompt = (
                    f"{admin_context}\n\n"
                    f"Conversation History:\n{history_str}\n"
                    f"User: {question}\n"
                    f"Assistant:"
                )
                
                res = safe_llm_complete(chat_prompt)
                response_text = res.text.strip()
                
                if response_text.startswith("NEED_SQL:"):
                    processed_question = response_text.replace("NEED_SQL:", "").strip()
                    print("\033[90mProcessing database query...\033[0m")
                    user_context = f"[Logged-in User context: Role={role}] "
                    response = query_engine.query(user_context + processed_question)
                    
                    sql_query = response.metadata.get("sql_query")
                    
                    print("\n" + "="*50)
                    if sql_query:
                        print(f"\033[93mGenerated SQL Query:\033[0m\n{sql_query}")
                        print("-"*50)
                    else:
                        print("\033[91mNo SQL query was generated.\033[0m")
                        print("-"*50)
                        
                    print(f"\033[92mAnswer:\033[0m\n{response.response}")
                    print("="*50 + "\n")
                    chat_history.append((question, response.response))
                else:
                    print("\n" + "="*50)
                    print(f"\033[92mAnswer:\033[0m\n{response_text}")
                    print("="*50 + "\n")
                    chat_history.append((question, response_text))
            
            else: # trainer role
                print("\033[90mProcessing question...\033[0m")
                history_str = ""
                for prev_q, prev_a in chat_history[-5:]:
                    history_str += f"User: {prev_q}\nAssistant: {prev_a}\n"
                
                chat_prompt = (
                    f"{trainer_context}\n\n"
                    f"Conversation History:\n{history_str}\n"
                    f"User: {question}\n"
                    f"Assistant:"
                )
                
                res = safe_llm_complete(chat_prompt)
                response_text = res.text
                
                print("\n" + "="*50)
                print(f"\033[92mAnswer:\033[0m\n{response_text}")
                print("="*50 + "\n")
                chat_history.append((question, response_text))
                
        except KeyboardInterrupt:
            print("\n\033[96mGoodbye!\033[0m")
            break
        except Exception as e:
            print(f"\n\033[91mAn error occurred during query processing: {e}\033[0m\n")

if __name__ == "__main__":
    main()
