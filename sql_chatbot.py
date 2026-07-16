import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine
from llama_index.core import SQLDatabase, Settings, PromptTemplate
from llama_index.core.embeddings import MockEmbedding
from llama_index.llms.gemini import Gemini
from llama_index.core.query_engine import NLSQLTableQueryEngine

load_dotenv()

def main():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("\033[91mError: GEMINI_API_KEY environment variable is not set.\033[0m")
        print("Please set your Gemini API key in the .env file or environment.")
        sys.exit(1)

    print("\033[96mInitializing SQL Chatbot...\033[0m")
    
    try:
        llm = Gemini(model="models/gemini-3.1-flash-lite", api_key=api_key)
        Settings.llm = llm
        Settings.embed_model = MockEmbedding(embed_dim=768)
    except Exception as e:
        print(f"\033[91mError initializing Gemini LLM: {e}\033[0m")
        sys.exit(1)

    db_file = "profice.db"
    if not os.path.exists(db_file):
        print(f"\033[91mError: Database file '{db_file}' not found in the current directory.\033[0m")
        sys.exit(1)
        
    try:
        engine = create_engine(f"sqlite:///{db_file}")
        sql_database = SQLDatabase(engine, include_tables=["trainers", "feedback"])
    except Exception as e:
        print(f"\033[91mError connecting to database: {e}\033[0m")
        sys.exit(1)

    try:
        query_engine = NLSQLTableQueryEngine(
            sql_database=sql_database,
            tables=["trainers", "feedback"],
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
            "to match the context of the database tables before writing the SQL query.\n\n"
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

    print("\033[92mChatbot initialized successfully!\033[0m")
    print("Database: \033[93mprofice.db\033[0m (Tables: trainers, feedback)")
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
            
            classification_prompt = (
                f"You are a routing assistant. Classify the user's question into one of two labels:\n"
                f"- 'DB_QUERY': The question asks for data, summaries, reports, or ratings about trainers, feedback, or departments.\n"
                f"- 'CHITCHAT': General greetings, hello, goodbye, off-topic chat, or questions unrelated to the database.\n\n"
                f"Only output the label ('DB_QUERY' or 'CHITCHAT') and nothing else.\n\n"
                f"User Question: {question}\n"
                f"Label:"
            )
            
            route_res = Settings.llm.complete(classification_prompt)
            label = route_res.text.strip().upper()
            
            if "CHITCHAT" in label:
                chat_res = Settings.llm.complete(
                    f"You are a helpful SQL database assistant. Respond to the user's message conversationally. "
                    f"Remind them that you can help them query trainer and feedback data in the database.\n\n"
                    f"User: {question}\n"
                    f"Assistant:"
                )
                print("\n" + "="*50)
                print(f"\033[92mAnswer:\033[0m\n{chat_res.text}")
                print("="*50 + "\n")
                continue
            
            print("\033[90mProcessing question...\033[0m")
            response = query_engine.query(question)
            
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
            
        except KeyboardInterrupt:
            print("\n\033[96mGoodbye!\033[0m")
            break
        except Exception as e:
            print(f"\n\033[91mAn error occurred during query processing: {e}\033[0m\n")

if __name__ == "__main__":
    main()
