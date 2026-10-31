# AI-Driven Enterprise Project Intelligence & Risk Management Platform

Milestone 1 is a local-first full-stack application for ingesting mixed-format documents, extracting and chunking their text, creating sentence-transformer embeddings, persisting vectors in ChromaDB, and retrieving source-linked chunks through semantic search.

## Problem statement

Enterprise project information is often distributed across documents and spreadsheets, making it difficult to find relevant evidence, track project status, and spot delivery risks. This project provides a local application for organizing that information and surfacing searchable, source-linked evidence.

## Objectives

- Ingest supported project documents and preserve their source locations.
- Extract and chunk document text, then persist metadata and vector embeddings locally.
- Make project information explorable through document, chunk, embedding, and semantic-search views.
- Provide rule-based scope, risk, and blocker findings grounded in indexed project documents.

## Implemented features

- Upload, parse, and manage supported document formats; inspect extracted text and chunks.
- Persist document records and chunks in SQLite and embeddings in ChromaDB.
- Generate sentence-transformer embeddings and run semantic nearest-neighbor search with source-linked results.
- Explore indexed chunks and embedding vectors in the web interface.
- Run scope/deliverable, risk, and blocker/action-item analysis with evidence excerpts and source references.
- Configure local processing limits, chunking, model selection, data paths, and CORS through environment variables.

Analysis agents currently use local rule-based extraction. Search retrieves relevant source chunks; the application does not currently generate LLM-written answers. These distinctions are intentional and are not planned features represented as implemented functionality.

## Architecture

- `frontend/`: React, TypeScript, Vite, React Router, Axios, responsive local UI.
- `backend/app/`: FastAPI API, format-specific extraction, approximate-token chunking, embedding and retrieval services.
- SQLite stores document metadata, extracted text, chunk text, processing state, locations, and embedding/index status.
- ChromaDB stores the actual normalized vectors and lookup metadata under `data/chroma_db/`.
- Uploaded source files and generated local indexes are ignored by Git in `data/`.

### RAG pipeline

1. A user uploads supported files through the frontend; the FastAPI backend validates and stores each file locally.
2. Format-specific parsers extract text and source-location metadata. The chunking service divides the text into overlapping, approximately token-sized segments.
3. The embedding service encodes each chunk with the configured sentence-transformer model. Document/chunk metadata is persisted in SQLite, and vectors plus lookup metadata are stored in ChromaDB.
4. A semantic-search query is embedded with the same model and sent to ChromaDB. The API returns nearest chunks, distances, and source metadata for the frontend to display.
5. Scope, risk, and blocker analysis scans stored extracted text with local rule-based logic and links findings to supporting chunks where possible.

This implements ingestion, indexing, and retrieval portions of a retrieval-augmented workflow. It does not currently pass retrieved context to an LLM to synthesize an answer.

### Technologies

- **Frontend:** React 19, TypeScript, Vite, React Router, Axios, and Lucide icons.
- **Backend:** Python, FastAPI, Pydantic, Uvicorn, and python-dotenv.
- **Document processing:** PyMuPDF, python-docx, pandas, openpyxl, xlrd, python-pptx, striprtf, and BeautifulSoup.
- **Storage and retrieval:** SQLite, ChromaDB, NumPy, and sentence-transformers.
- **Testing:** pytest and httpx for backend tests; TypeScript and Vite production build for frontend verification.

### Project structure

```text
backend/
  app/
    api/routes/       FastAPI endpoints for health, documents, chunks, search, and analysis
    core/             Environment-based application settings
    database/         SQLite initialization and connection helpers
    schemas/          API request/response models
    services/         Parsing, chunking, embeddings, vector storage, and analysis
  tests/              Backend API, processing, and project-agent tests
frontend/
  src/
    components/       Shared UI components
    layouts/          Application layout and navigation
    pages/            Dashboard, document, search, exploration, and analysis screens
    services/         Backend API client
data/                 Local runtime data (ignored by Git; created as needed)
.env.example          Safe configuration template
```

### Project workflow

Upload documents, inspect their processing status, browse document text and chunks, explore embeddings, and search for source evidence. Use the Project Intelligence screens to run scope, risk, and blocker analysis and follow findings back to their evidence. Backend API calls and their schemas are available through the interactive OpenAPI documentation.

The embedding model is loaded lazily on the first indexing or search request; it is downloaded by `sentence-transformers` when it is not already cached. There are no sample/fake records in the application. API tests use deterministic embedding fixtures to test the pipeline and persistent vector-store behavior without downloading the model on every test run.

## Requirements

- Windows 10/11 and PowerShell.
- Python 3.11 or newer. The implementation is being validated with Python 3.14; if a native ML dependency does not yet publish a compatible wheel for your Python installation, use Python 3.12.
- Node.js 20.19+ or 22.12+.
- Network access for initial dependency and embedding-model downloads.
- Optional: LibreOffice on `PATH` for legacy `.doc` and `.ppt` conversion.

## Installation (PowerShell)

Open this folder in VS Code. In a PowerShell terminal at the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
```

The application defaults to its local `data\` directory; edit `.env` only when changing the limits, paths, CORS origins, chunk configuration, or model. If installed ML wheels do not support your Python release, install Python 3.12 and create the environment with `py -3.12 -m venv .venv`.

Install frontend packages in a second PowerShell terminal:

```powershell
Set-Location frontend
npm install
```

## Start the application

Terminal 1 (project root, with the Python environment activated):

```powershell
Set-Location backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2:

```powershell
Set-Location frontend
npm run dev
```

Open the Vite URL `http://127.0.0.1:5173`. API health is at `http://127.0.0.1:8000/health`; interactive Swagger docs are at `http://127.0.0.1:8000/docs`.

## Use the workflow

1. Open **Upload documents**, browse or drag-and-drop one or more files of different supported types, and submit the batch. The default limits are 25 files per batch and 50 MB per file.
2. Processing runs independently for each file. The result summary reports indexed and failed files; files with a registered document ID can be retried. Legacy `.doc` and `.ppt` require LibreOffice.
3. In **Documents**, open a record to view extracted text and persisted chunks. The Chunks column shows actual ChromaDB indexed chunks over persisted extracted chunks; the workspace indexed total is sourced from ChromaDB vector IDs and document metadata. The document details page survives browser refresh.
4. In **Chunk explorer**, search chunk contents and filter by source or file type. Its indexed total uses the same ChromaDB count as Documents, while stored matches count extracted SQLite chunk records. Click a row for full text and metadata.
5. In **Embeddings**, select a chunk to view its actual vector dimensions and first 20 values; choose **Load full vector** only when needed.
6. In **Semantic search**, submit a natural-language query, choose top K, and optionally filter to a document or file type. Results include actual chunk text, source location, and raw cosine distance (lower is closer; it is not a similarity percentage).
7. Use the dark responsive sidebar to open the overview, documents, semantic search, and individual project-agent views. The overview's Milestone 2 metrics come from live agent API responses; unavailable agent metrics are labeled rather than estimated.

## API endpoints

The interactive API reference is available from FastAPI Swagger UI. Main routes:

- `GET /health`, `GET /stats`, `GET /supported-formats`
- `POST /documents/upload`, `GET /documents`, `GET /documents/{document_id}`
- `GET /documents/{document_id}/text`, `GET /documents/{document_id}/chunks`
- `POST /documents/{document_id}/reprocess`, `DELETE /documents/{document_id}`
- `GET /chunks`, `GET /chunks/{chunk_id}`, `GET /chunks/{chunk_id}/embedding?full=false`
- `POST /search`
- `POST /analysis/scope`, `POST /analysis/risks`, `POST /analysis/blockers`

## Milestone 2: project intelligence agents

The platform now includes three evidence-grounded analysis agents that scan the full extracted text stored for indexed documents and match findings to persisted document chunks for source evidence. Agent analysis does not query ChromaDB for semantic top-k retrieval.

- Scope and deliverables agent: extracts goals, milestones, responsibilities, dependencies, and progress statements while preserving source-document references.
- Risk detection agent: identifies schedule, dependency, and delivery risks with default rule-based classification (`Low`, `Medium`, `High`, `Critical`) and recommends mitigation steps.
- Blocker and action item agent: extracts explicitly labeled findings and direct blocker, decision, and commitment wording, with dates, priorities, and support evidence only when the documents support them.
- Finding cards in all three views open a shared detail panel with the agent/category, relevant status, recommendations, evidence excerpt, and source location. When an indexed chunk is available, **Open source document** opens that document with the cited chunk expanded.

The scope, risk, and blocker endpoints currently use local rule-based extraction regardless of `AI_PROVIDER`. Although provider configuration and an LLM helper exist, these analysis paths do not call that helper; setting cloud credentials does not currently change agent findings.

## Tests and build

At the project root, activate the virtual environment and run:

```powershell
Set-Location backend
python -m pytest
```

Frontend production type-check/build:

```powershell
Set-Location frontend
npm run build
```

The API integration tests create an isolated temporary SQLite database and persistent Chroma directory. They replace the expensive transformer inference with deterministic test vectors; they still exercise actual Chroma upsert, query, persistence, retry, and deletion. To separately run the real model embedding check (and permit its first model download), run from `backend`:

```powershell
$env:RUN_MODEL_INTEGRATION = "1"
python -m pytest tests\test_processing.py -k real_embedding
Remove-Item Env:\RUN_MODEL_INTEGRATION
```

## Troubleshooting

- **Python package or PyTorch wheel is unavailable:** use Python 3.12, create a fresh `.venv`, and reinstall `backend\requirements.txt`.
- **First upload/search is slow or fails to download the model:** allow network access to the model host, retry, and inspect the backend log. The model is loaded lazily; `/health` remains useful while it is not cached.
- **Port is in use:** stop the process using port 8000 or 5173, or change the corresponding Uvicorn/Vite port and update `CORS_ORIGINS` and `VITE_API_URL`.
- **SQLite/Chroma cannot write:** ensure the project `data\` folder is writable. Set `APP_DATA_DIR` in `.env` to another writable local path if necessary.
- **Legacy Office format reports a conversion requirement:** install LibreOffice and make `soffice.exe` available on `PATH`, then restart the backend.
- **CORS error in a custom frontend origin:** add that exact origin to `CORS_ORIGINS` and restart FastAPI.

## Known limitations

- Chunk size and overlap use a documented regex-based token approximation rather than the embedding model's tokenizer. Spreadsheet rows and source locations are included in the chunk text/metadata.
- Processing is synchronous in the upload request and intended for local milestone-scale use, not high-throughput concurrent workloads.
- Scanned/image-only PDFs are reported as non-extractable; OCR is not performed.
- LibreOffice conversion is optional and is required for `.doc` and `.ppt`; `.xls` support uses `xlrd`.
- Retrieval returns nearest source chunks, not an LLM-generated answer.
- The UI test framework is not added; frontend verification currently consists of the TypeScript/Vite production build and live API workflow checks.

## Future enhancements (planned)

The following are potential follow-on improvements and are not implemented in the current source:

- Generate grounded natural-language answers from retrieved chunks using a configured LLM provider.
- Add OCR for scanned or image-only documents.
- Move document processing to background jobs for larger workloads.
- Add authentication, authorization, and deployment/operations support for shared environments.
