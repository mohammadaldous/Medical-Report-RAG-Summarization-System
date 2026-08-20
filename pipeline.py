import os
import re
import logging
import uuid
import pandas as pd
from pypdf import PdfReader
import chromadb
from openai import OpenAI

# إعداد ملف السجل (Log File)
logging.basicConfig(
    filename="system_pipeline.log",
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    encoding="utf-8"
)

def log_event(message: str, level: str = "info"):
    if level == "warning":
        logging.warning(message)
    elif level == "error":
        logging.error(message)
    else:
        logging.info(message)

# استخراج النصوص من مختلف التنسيقات (TXT, PDF, Excel)
def extract_text_from_file(uploaded_file) -> str:
    filename = uploaded_file.name.lower()
    text = ""
    try:
        if filename.endswith(".txt"):
            text = uploaded_file.read().decode("utf-8")
        elif filename.endswith(".pdf"):
            reader = PdfReader(uploaded_file)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file)
            text = df.to_string(index=False)
        return text
    except Exception as e:
        log_event(f"Error reading file {uploaded_file.name}: {str(e)}", level="error")
        return ""

# فحص جودة وصحة البيانات وفق القواعد المحددة
def validate_patient_data(text: str) -> tuple[bool, str, dict]:
    log_event("Starting data validation pipeline...")

    name_match = re.search(r"Patient Name\s*:\s*([^\n\r,]+)", text, re.IGNORECASE)
    age_match = re.search(r"Age\s*:\s*(-?\d+)", text, re.IGNORECASE)

    if not name_match:
        msg = "Validation Failed: 'Patient Name' field was not found in the report."
        log_event(msg, level="warning")
        return False, msg, {}

    if not age_match:
        msg = "Validation Failed: 'Age' field was not found in the report."
        log_event(msg, level="warning")
        return False, msg, {}

    raw_name = name_match.group(1).strip()
    raw_age_str = age_match.group(1).strip()

    clean_name = raw_name.replace(" ", "")
    if not clean_name.isalpha():
        msg = f"Validation Rejected: Patient name '{raw_name}' contains invalid characters, digits, or symbols."
        log_event(msg, level="warning")
        return False, msg, {}

    try:
        age = int(raw_age_str)
        if age < 0 or age > 120:
            msg = f"Validation Rejected: Invalid age ({age}). Age must be between 0 and 120."
            log_event(msg, level="warning")
            return False, msg, {}
    except ValueError:
        msg = f"Validation Rejected: Age '{raw_age_str}' is not a valid number."
        log_event(msg, level="warning")
        return False, msg, {}

    parsed_info = {"name": raw_name, "age": age}
    msg = f"Validation Passed for patient: {raw_name}, Age: {age}."
    log_event(msg, level="info")
    return True, msg, parsed_info

# تهيئة قاعدة البيانات الشعاعية الدائمة ChromaDB
def setup_knowledge_base(kb_file_path: str = "hospital_knowledge.txt"):
    client = chromadb.PersistentClient(path="./chromadb_data")
    kb_collection = client.get_or_create_collection(name="hospital_kb")
    patient_collection = client.get_or_create_collection(name="patient_records")

    if os.path.exists(kb_file_path) and kb_collection.count() == 0:
        with open(kb_file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
        kb_collection.add(
            documents=chunks,
            ids=[f"kb_chunk_{i}" for i in range(len(chunks))]
        )
        log_event(f"Initialized Knowledge Base with {len(chunks)} chunks.")
        
    return kb_collection, patient_collection

# حفظ التقرير والملخص في الـ Vector Database
def save_patient_record(patient_collection, patient_info: dict, report_text: str, summary_text: str):
    record_id = str(uuid.uuid4())
    combined_content = f"Patient: {patient_info['name']} | Age: {patient_info['age']}\nReport:\n{report_text}\nSummary:\n{summary_text}"
    
    patient_collection.add(
        documents=[combined_content],
        metadatas=[{
            "patient_name": patient_info["name"],
            "patient_age": patient_info["age"],
            "record_id": record_id
        }],
        ids=[record_id]
    )
    log_event(f"Successfully saved record for {patient_info['name']} into ChromaDB (ID: {record_id}).")
    return record_id

# استرجاع سياق الـ RAG وتوليد الملخص الطبي
def generate_physician_summary(report_text: str, api_key: str, collection) -> str:
    log_event("Starting RAG retrieval and summary generation...")
    
    results = collection.query(query_texts=[report_text], n_results=2)
    retrieved_context = "\n---\n".join(results["documents"][0]) if results["documents"] else ""

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
    
    prompt = f"""
You are an expert AI clinical assistant. Your task is to generate a clear, concise, and structured medical summary for a physician in Arabic.

[HOSPITAL KNOWLEDGE BASE / GUIDELINES]:
{retrieved_context}

[PATIENT REPORT CONTENT]:
{report_text}

[INSTRUCTIONS]:
1. Provide a professional, structured overview in Arabic:
   - معلومات المريض (الاسم والعمر)
   - الأعراض والشكوى الرئيسية
   - العلامات الحيوية والتحاليل ومقارنتها بالإرشادات الطبية
   - الخطة والتوصيات المقترحة للطبيب
2. Keep the summary direct, objective, and structured.
"""

    response = client.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional medical summarization assistant."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    )

    summary = response.choices[0].message.content
    log_event("Summary generated successfully.")
    return summary

# دالة الشات بوت للإجابة عن أسئلة التقرير ومنع التخمين
def answer_patient_question(report_text: str, user_question: str, api_key: str, chat_history: list) -> str:
    log_event(f"Processing chatbot question: {user_question}")
    
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    system_instruction = """
أنت مساعد طبي ذكي تجيب عن أسئلة الطبيب أو المستخدم بناءً على تقرير المريض المرفق فقط.

القواعد الصارمة:
1. اعتمد بشكل كامل وحصري على البيانات الموجودة في نص التقرير المرفق أدناه.
2. إذا كان السؤال عن معلومة غير موجودة في التقرير (مثل: لم يذكر اسم الدواء، أو تاريخ، أو مرض غير مكتوب)، يجب أن تجيب بوضوح: "المعلومة غير متوفرة في التقرير الطبي المرفق." ولا تخمّن أو تختلق أي معلومة نهائياً.
3. جاوب باللغة العربية باختصار ودقة ومباشرة.
"""

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"[نص تقرير المريض المعتمد]:\n{report_text}\n\nالسياق أعلاه هو المرجع الوحيد للإجابة."}
    ]

    # إضافة المحادثة السابقة
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # إضافة السؤال الحالي
    messages.append({"role": "user", "content": user_question})

    response = client.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=messages,
        temperature=0.0
    )

    answer = response.choices[0].message.content
    log_event("Chatbot answered question.")
    return answer