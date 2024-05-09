from fastapi import FastAPI, status, File, UploadFile, HTTPException
from vecdb_utils import RetrieverBase
from api_schemas import CreateCollectionSchema, AddDocstoCollectionSchema, QueryCollectionSchema
from embedding_funcs import TfIdf_EmbeddingFunction, DummyEmbeddingFunction
from pdf_utils import read_pdf_pages

import os
import datetime
from dotenv import load_dotenv
import chromadb



#Settings
DB_PORT=8000
DB_HOST="localhost"

STORAGE_DIR_PATH = "/app/docs"



#For Docker test
db_client = chromadb.PersistentClient(path="/app/chroma_data")



#For some reason, chroma runs its own default embedder; need to roll back and fix it


embedding_func = TfIdf_EmbeddingFunction(path_to_vectorizer="/app/tfidf_vec.joblib")

#init retrieval
#TODO: make collection dict inside persistent (mb read from db for init)
retrieval = RetrieverBase(db_client=db_client)

#create tmp dir
if not os.path.exists(STORAGE_DIR_PATH):
	os.makedirs(STORAGE_DIR_PATH)
	

app = FastAPI()

#Endpoints
@app.get("/")
def home():
	return {
		"info" : "Retrieval service is running",
		"status" : "healthy",
		"timestamp" : datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S"),
		"db_heartbeat" : db_client.heartbeat()
	 	}



@app.post("/collections/create")
def create_collection(create_query : CreateCollectionSchema):
	
	retrieval.create_collection(collection_name=create_query.collection_name,
							    embedding_func=embedding_func,
								metadata=create_query.metadata)
	
	return {"message" : f"Collection {create_query.collection_name} is created"}


@app.post("/collections/add/{collection_name}")
def add_docs_to_collection(collection_name, add_request : AddDocstoCollectionSchema):
	
	retrieval.load_docs_to_collection(collection_name=collection_name,
								   	  docs=add_request.docs,
								      metadatas=add_request.metadatas)
	
	return {"message" : f"{len(add_request.docs)} docs were added to {collection_name}"}


@app.post("/collections/query/{collection_name}")
def query_collection(collection_name, query_request : QueryCollectionSchema):
	
	query_result = retrieval.query_collection(collection_name=collection_name,
										      query=query_request.query,
											   n_results=query_request.n_results)
	
	return query_result


@app.post("/collections/upload_doc/{collection_name}")
def upload_pdf_to_collection(collection_name, file : UploadFile = File(...)):

	if file.content_type != 'application/pdf':
		return {"message": "This endpoint accepts only PDF files."}
	
	#extract content
	content = file.file.read()

	#save to tempdir
	with open(os.path.join(STORAGE_DIR_PATH, file.filename), "wb") as f:
		f.write(content)
	

	#read pdf and load to db
	pages, metadatas = read_pdf_pages(os.path.join(STORAGE_DIR_PATH, file.filename))
	retrieval.load_docs_to_collection(collection_name=collection_name,
								    docs=pages,
									metadatas=metadatas)
	
	return {"message" : f"{len(pages)} pages of {file.filename} were loaded to collection"}


