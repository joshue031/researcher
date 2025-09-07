def get_metadata_extraction_prompt(text_content, doc_type):
    """
    Creates a prompt to extract metadata fields as a JSON object.
    """
    if doc_type == 'journal_article':
        fields_to_extract = "author, title, journal, year, volume, pages"
        example_json = """{
          "title": "Example Paper Title",
          "author": "First Author and Second Author",
          "year": "2023",
          "journal": "Journal of Examples",
          "volume": "10",
          "pages": "1-15",
          "description": "This paper describes an example methodology for testing systems."
        }"""
    else: # Generic book/report template
        fields_to_extract = "author, title, year, howpublished"
        example_json = """{
          "title": "Title of the Report",
          "author": "Author Name",
          "year": "2024",
          "howpublished": "Example Publisher Inc.",
          "description": "This report outlines the key findings of a study on examples."
        }"""

    prompt = f"""
    From the document text provided below, extract the following bibliographic details: {fields_to_extract}.
    Also, write a concise, one-sentence summary of the document's content for the "description" field.

    Return the information as a single, valid JSON object.
    Here is an example of the desired format:
    {example_json}

    If a value for a specific field cannot be found in the text, return an empty string "" for that field's value.

    Document Text:
    ---
    {text_content}
    ---

    Respond with ONLY the JSON object.
    """
    return prompt

def get_rag_prompt(question, context):
    """
    Creates a prompt for the RAG system to answer a question based on provided context.
    """
    prompt = f"""
    Based on the following context, please provide a comprehensive answer to the user's question.
    If the context does not contain the answer, please advise the user, but still answer the question to the best of your knowledge.
    Context:
    ---
    {context}
    ---
    Question: {question}
    """
    return prompt

def get_figure_analysis_prompt():
    """
    Creates a robust, simplified prompt for analyzing a technical figure.
    This version avoids providing a full example to prevent attentional bias.
    """
    prompt = """
    You are an expert research assistant. Your task is to analyze the provided image, which is a figure from a technical document, and provide a structured analysis in JSON format.

    Carefully examine the image and respond with ONLY a single, valid JSON object.

    Your JSON response must contain these exact keys:
    - "name": A name of the figure. If there is accompanying text with a figure number such as "Figure 2", use that. Otherwise, come up with a short, descriptive name.
    - "description": A concise, one-to-two-sentence string describing what the figure depicts.
    - "analysis": A string explaining the main scientific takeaway or conclusion from the figure.
    - "extracted_text": A string containing all text transcribed from the image (axis labels, legends, etc.).

    Do not include any text or explanations outside of the JSON object. Base your entire analysis on the content of the image provided.
    """
    return prompt