"""One-off script: chunk + embed a folder of docs (.txt/.md/.pdf) into Qdrant. Run once: python ingest_docs.py <folder>"""
import sys
import time
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()
from rag import add_documents_batch, list_sources

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)


def read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def ingest_file(chunks: list[str], source: str):
    for attempt in range(3):
        try:
            result = add_documents_batch(chunks, source)
            print(f"{source}: {len(result['ids'])} chunks")
            return
        except Exception as e:
            if attempt == 2:
                print(f"{source}: FAILED after 3 attempts — {e}")
                return
            print(f"{source}: retry {attempt + 1} ({e})")
            time.sleep(3)


folder = Path(sys.argv[1] if len(sys.argv) > 1 else "docs")
already_done = set(list_sources())

for path in sorted(folder.glob("**/*")):
    if not path.is_file():
        continue
    suffix = path.suffix.lower()
    source = path.stem
    if source in already_done:
        print(f"{source}: skip (already ingested)")
        continue

    if suffix == ".pdf":
        text = read_pdf(path)
    elif suffix in (".txt", ".md"):
        text = path.read_text(errors="ignore")
    else:
        continue

    chunks = splitter.split_text(text)
    if not chunks:
        print(f"{source}: skip (no extractable text)")
        continue

    ingest_file(chunks, source)

print("Done.")
