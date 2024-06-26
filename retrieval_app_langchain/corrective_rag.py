from langchain_community.embeddings.sentence_transformer import SentenceTransformerEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain.schema import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableParallel
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_community.chat_models.openai import ChatOpenAI
from langchain_community.retrievers import TavilySearchAPIRetriever
from langchain.callbacks import get_openai_callback
from langgraph.graph import END, StateGraph


from dotenv import load_dotenv
from typing_extensions import TypedDict
from retrieval import create_ragchain
from typing import List
import os
import chromadb

load_dotenv()

CHROMA_DB_HOST="localhost"
CHROMA_DB_PORT=8012
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")



embedding_func = SentenceTransformerEmbeddings(model_name="all-MiniLM-L6-v2")

llm = ChatOpenAI(model="gpt-3.5-turbo",
                  temperature=0.2,
                    max_tokens=500,
                    api_key=OPENAI_API_KEY,
                    )





splits = ["Hello everyone! I am Stanislav and I study Computer Science",
          "Hey, my name is Anna. I live in Russia and study Psychology"]

vectorstore = Chroma.from_texts(texts=splits, embedding=embedding_func)
retriever = vectorstore.as_retriever(search_type="mmr")


tavily_search_retriever = TavilySearchAPIRetriever(k=4,
                                                   api_key=TAVILY_API_KEY)


class GraphState(TypedDict):
    """
    Represents the state of our graph.

    Attributes:
        question: question
        generation: LLM generation
        web_search: whether to add search
        documents: list of documents
    """

    question: str
    generation: str
    web_search: str
    documents: List[str]
    workflow_steps: List

##PROMTS
RAG_PROMT = PromptTemplate.from_template(
    """
        Context:
            {docs}
        Question:
            {question}
       You are a helpful expert in answering questions related to provided context.
       If provided documents do not relate to the question, feel free to answer the question yourself.
      """
)
    

GRADER_PROMT =  PromptTemplate(
    template="""You are a grader assessing relevance of a retrieved document to a user question. \n 
    Here is the retrieved document: \n\n {document} \n\n
    Here is the user question: {question} \n
    If the document contains keywords related to the user question, grade it as relevant. \n
    It does not need to be a stringent test. The goal is to filter out erroneous retrievals. \n
    Give a binary score 'yes' or 'no' score to indicate whether the document is relevant to the question. \n
    Provide the binary score as a JSON with a single key 'score' and no premable or explanation.""",
    input_variables=["question", "document"]
    )

REWRITER_PROMT = PromptTemplate(
    template="""You are a question-rewriter expert that can rewrite an input question to be
    a web search query. Make sure to keep only relevant informative words to optimize web search.\n
    Here is the input question:\n{question}\n
    Optimizer query for websearch: """,
    input_variables=["question"]
)

##CREATE CHAINS 
rag_chain = (RAG_PROMT | llm | StrOutputParser())


grader_chain = (GRADER_PROMT | llm | JsonOutputParser())

rewriter_chain = (REWRITER_PROMT | llm | StrOutputParser())



##DEFINING ACTIONS FOR NODES IN GRAPH
def retrieve(state : GraphState):
    """
    Retrieve documents

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): New key added to state, documents, that contains retrieved documents
    """

    print("-------RETRIEVE---------------")
    question = state["question"]
    print("Quetion: ", question)
    documents = retriever.get_relevant_documents(query=question)

    #write history
    history = []

    history.append({"node_name" : "retrieve",
                    "question" :  question,
                    "documents" : documents,
                    })
    


    return {"documents" : documents,
            "question" : question,
            "workflow_steps" : history}




def generate(state):
    """generates output based on provided context"""

    print("-----GENERATE------")
    generation = rag_chain.invoke({"docs" : state["documents"],
                                   "question" : state["question"]})

    print("Answer: ", generation)


    #history
    history = state["workflow_steps"]
    history.append({"node_name" : "generate",
                    "answer" : generation})

    return {"documents" : state["documents"],
            "question" : state["question"],
            "generation" : generation,
            "workflow_steps" : history}

#todo: make loop asyncronous
def grade_documents(state):
    
    documents = state["documents"]

    filtered_docs = []
    scores = []
    web_search = "No"
    
    print("----GRADE DOCS------")
    for doc in documents:

        score_result = grader_chain.invoke({"question" : state["question"],
                                            "document" : doc.page_content})
        print("Document is relevant: ", score_result["score"])
        scores.append(score_result)
        if score_result["score"] == "yes":
            filtered_docs.append(doc)
    
    if len(filtered_docs) <= 2:
        web_search = "Yes"


    print("Relevant docs count: ", len(filtered_docs))
    print(filtered_docs)

    return {"documents" : filtered_docs,
            "question" : state["question"],
            "generation" : state["generation"],
            "web_search" : web_search}



def decide_to_generate(state):
    """Router that decides whether generate or not"""
    
    if state["web_search"] == "Yes":
        print("DECISION: Do web search")
        return "transform_query"
    
    elif state["web_search"] == "No":
        print("DECISION: Generate")
        return "generate"
    


def rewrite_query_for_websearch(state):
    
    question = state["question"]

    print("---REWRITE QUESTION-----")
    optimized_question  = rewriter_chain.invoke({"question" : question})
    print("Optimized question: ", optimized_question)

    return {"documents" : state["documents"],
            "question" : optimized_question}    


#CONSIDER DuckDuckGO search / add Wikipedia as +1 option
def search_on_web(state):

    print("----WEB_SEARCH-----")

    docs = state["documents"]
    question = state["question"]


    websearch_results = tavily_search_retriever.invoke(question)

    #combine websearch results 
    websearch_results = "\n".join([doc.page_content for doc in websearch_results])
    websearch_results = Document(page_content=websearch_results)

    docs.append(websearch_results)

    return {"documents" : docs,
            "question" : question,}


def build_crag_graph():
    workflow = StateGraph(GraphState)

    #define nodes
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("generate", generate)
    workflow.add_node("grade", grade_documents)
    workflow.add_node("transform_query", rewrite_query_for_websearch)
    workflow.add_node("websearch", search_on_web)

    #set up edges
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "grade")
    workflow.add_conditional_edges("grade",
                                decide_to_generate,
                                path_map={"transform_query": "transform_query",
                                            "generate": "generate"})

    workflow.add_edge("transform_query", "websearch")
    workflow.add_edge("websearch", "generate")
    workflow.add_edge("generate", END)

    app = workflow.compile()

    return app


##TEST 
# Define the documents
documents = [
    "Python is a versatile programming language that is widely used in data science, web development, and automation.",
    "Bangkok is the capital of Thailand. It is the largest city. Bangkok is a wonderful place for tourists.",
    "Machine learning engineers are in high demand in the tech industry. They often work with data scientists to build predictive models.",
    "JavaScript is a popular language for building interactive web applications. It is used by front-end developers.",
    "The job market for machine learning engineers is growing rapidly, with many opportunities in various industries."
]

# Define the question
question = "What is machine learning? How useful ML might be for business?"

# Wrap the documents in the expected Document class
documents_wrapped = [Document(page_content=doc) for doc in documents]
retriever.add_documents(documents_wrapped)


crag_app = build_crag_graph()

from pprint import pprint

# Run
inputs = {"question": question}


# # Final generation
answer = crag_app.invoke(inputs)

pprint(answer)
print("----ANSWER----")
pprint(answer["generation"])
 
##TODO: Think about memory for QA bot
##TODO: start to develop frontend in NextJS

