import pypdf
from langchain_text_splitters import RecursiveCharacterTextSplitter

def load_and_split_document(file_path):
    """Loads a document and splits it into manageable chunks."""
    try:
        if file_path.lower().endswith('.pdf'):
            loader = pypdf.PdfReader(file_path)
            text = "".join(page.extract_text() for page in loader.pages)
        else: # Assume text file
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()

        if not text:
            return []

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
            length_function=len
        )
        chunks = text_splitter.split_text(text)
        # print("DOCUMENT IS")
        # print(chunks)
        return chunks
    except Exception as e:
        print(f"Error processing file {file_path}: {e}")
        return []
