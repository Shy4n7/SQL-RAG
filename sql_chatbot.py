import os
import sys
import time
import json
import sqlite3
import difflib
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from llama_index.core import SQLDatabase, Settings, PromptTemplate
from llama_index.core.embeddings import MockEmbedding
from llama_index.llms.gemini import Gemini
from llama_index.core.query_engine import NLSQLTableQueryEngine

load_dotenv()

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
                print(f"\033[91mError: Name '{trainer_name}' is ambiguous. Multiple trainers found:\033[0m")
                for _, name in sub_matches:
                    print(f"- {name}")
                print("Please enter the full name.\n")
                continue
            elif len(sub_matches) == 1:
                trainer_id, matched_name = sub_matches[0]
                print(f"\033[92mAuthenticated as Trainer: {matched_name} (ID: {trainer_id})\033[0m")
                break

            t_names = [t[1] for t in trainers]
            close_matches = difflib.get_close_matches(trainer_name, t_names, n=3, cutoff=0.5)
            if len(close_matches) > 1:
                print(f"\033[91mError: Name '{trainer_name}' not found. Did you mean one of these?\033[0m")
                for name in close_matches:
                    print(f"- {name}")
                print()
                continue
            elif len(close_matches) == 1:
                matched_name = close_matches[0]
                trainer_id = [t[0] for t in trainers if t[1] == matched_name][0]
                print(f"\033[93mFuzzy matched input to: {matched_name}\033[0m")
                print(f"\033[92mAuthenticated as Trainer: {matched_name} (ID: {trainer_id})\033[0m")
                break
            else:
                print("\033[91mError: Trainer name not recognized. Registered trainers are:\033[0m")
                for _, name in trainers:
                    print(f"- {name}")
                print()
        else:
            print("Invalid choice. Please enter 1 or 2.")

    print("\033[96mInitializing SQL Chatbot...\033[0m")
    
    try:
        llm = Gemini(model="models/gemini-3.1-flash-lite", api_key=api_key)
        Settings.llm = llm
        Settings.embed_model = MockEmbedding(embed_dim=768)
    except Exception as e:
        print(f"\033[91mError initializing Gemini LLM: {e}\033[0m")
        sys.exit(1)
        
    try:
        engine = create_engine(f"sqlite:///file:{db_file}?mode=ro&uri=true")
        with engine.begin() as conn:
            conn.execute(text("DROP VIEW IF EXISTS temp.v_trainers"))
            conn.execute(text("DROP VIEW IF EXISTS temp.v_feedback"))
            if role == "admin":
                conn.execute(text("CREATE TEMP VIEW v_trainers AS SELECT id, name, department FROM trainers"))
                conn.execute(text("CREATE TEMP VIEW v_feedback AS SELECT id, trainer_id, student_name, feedback_text, rating FROM feedback"))
            else:
                conn.execute(text(f"CREATE TEMP VIEW v_trainers AS SELECT id, name, department FROM trainers WHERE id = {trainer_id}"))
                conn.execute(text(f"CREATE TEMP VIEW v_feedback AS SELECT id, trainer_id, feedback_text, rating FROM feedback WHERE trainer_id = {trainer_id}"))
        
        sql_database = SQLDatabase(
            engine,
            schema="temp",
            include_tables=["v_trainers", "v_feedback"],
            view_support=True
        )
    except Exception as e:
        print(f"\033[91mError connecting to database: {e}\033[0m")
        sys.exit(1)

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
        
    except Exception as e:
        print(f"\033[91mError initializing Query Engine: {e}\033[0m")
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
            
            history_str = ""
            for prev_q, prev_a in chat_history[-3:]:
                history_str += f"User: {prev_q}\nAssistant: {prev_a}\n"
            
            classification_prompt = (
                f"You are an assistant that classifies and rewrites user inputs based on conversation history.\n\n"
                f"Your task is to:\n"
                f"1. Classify the latest question as either 'DB_QUERY' (asks for data/ratings/trainers from a database) or 'CHITCHAT' (greetings, general chat, or off-topic).\n"
                f"2. If it is a 'DB_QUERY', rewrite it to be a standalone, self-contained question resolving any pronouns (he, she, they, his, its) from context.\n"
                f"   - Rule: If the user asks for 'another', 'other', 'else', or 'different' entity (e.g. 'another Akash' or 'anyone else'), "
                f"rewrite the question to exclude the entities already discussed in the history (e.g. 'other than Akash L').\n"
                f"   - Rule: DO NOT carry over unrelated attributes (like department or rating of previous entities) unless explicitly requested.\n\n"
                f"Format your output exactly as a JSON object with two keys:\n"
                f"{{\n"
                f"  \"label\": \"DB_QUERY\" or \"CHITCHAT\",\n"
                f"  \"rewritten_question\": \"standalone question here (or empty string for CHITCHAT)\"\n"
                f"}}\n\n"
                f"Conversation History:\n{history_str}\n"
                f"Latest Question: {question}\n"
                f"JSON Output:"
            )
            
            route_res = safe_llm_complete(classification_prompt)
            parsed_data = parse_json_response(route_res.text)
            label = parsed_data.get("label", "DB_QUERY").upper()
            processed_question = parsed_data.get("rewritten_question", question)
            
            if "CHITCHAT" in label:
                chat_res = safe_llm_complete(
                    f"You are a helpful SQL database assistant. Respond to the user's message conversationally. "
                    f"Remind them that you can help them query trainer and feedback data in the database.\n\n"
                    f"User: {processed_question}\n"
                    f"Assistant:"
                )
                response_text = chat_res.text
                print("\n" + "="*50)
                print(f"\033[92mAnswer:\033[0m\n{response_text}")
                print("="*50 + "\n")
                chat_history.append((question, response_text))
                continue
            
            print("\033[90mProcessing question...\033[0m")
            response = query_engine.query(processed_question)
            
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
            
        except KeyboardInterrupt:
            print("\n\033[96mGoodbye!\033[0m")
            break
        except Exception as e:
            print(f"\n\033[91mAn error occurred during query processing: {e}\033[0m\n")

if __name__ == "__main__":
    main()
