import streamlit as st
import pipeline

st.set_page_config(page_title="Medical AI Assistant - RAG", page_icon="🩺", layout="wide")

st.title("🩺 Medical Report RAG & Summarization System")
st.caption("Automated Data Quality Validation + Persistent ChromaDB Storage + Interactive Chatbot")

# تهيئة قاعدة البيانات
kb_collection, patient_collection = pipeline.setup_knowledge_base()

# تهيئة سجل المحادثة
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# القائمة الجانبية
with st.sidebar:
    st.header("⚙️ Configuration")
    api_key = st.text_input("Enter OpenAI / OpenRouter API Key:", type="password", help="ضع مفتاح الـ API الخاص بك هنا")
    st.divider()
    
    st.subheader("📊 Database Stats")
    st.write(f"📁 Hospital KB Chunks: **{kb_collection.count()}**")
    st.write(f"💾 Saved Patient Records: **{patient_collection.count()}**")
    
    st.divider()
    if st.button("🗑️ Clear Chat History"):
        st.session_state["messages"] = []
        st.rerun()

    if st.checkbox("Show System Logs"):
        st.subheader("System Event Log")
        try:
            with open("system_pipeline.log", "r", encoding="utf-8") as log_file:
                st.code(log_file.read(), language="text")
        except FileNotFoundError:
            st.info("No logs generated yet.")

# واجهة رفع الملف
st.subheader("1. Upload Medical Report")
uploaded_file = st.file_uploader("Choose a file (TXT, PDF, Excel)", type=["txt", "pdf", "xlsx", "xls"])

if uploaded_file is not None:
    report_text = pipeline.extract_text_from_file(uploaded_file)
    
    with st.expander("📄 Raw Report Preview", expanded=False):
        st.text(report_text)

    if st.button("🚀 Process & Validate Report"):
        if not api_key:
            st.error("Please enter your API Key in the sidebar first.")
        else:
            with st.spinner("Running automated data quality checks..."):
                is_valid, validation_msg, patient_info = pipeline.validate_patient_data(report_text)

            st.subheader("2. Quality & Validation Check")
            if is_valid:
                st.success(f"✅ {validation_msg}")
                st.info(f"👤 **Patient:** {patient_info['name']} | 🎂 **Age:** {patient_info['age']}")

                st.session_state["is_valid"] = True
                st.session_state["patient_info"] = patient_info
                st.session_state["report_text"] = report_text
                st.session_state["messages"] = []

                with st.spinner("Retrieving hospital guidelines & generating clinical summary..."):
                    summary = pipeline.generate_physician_summary(report_text, api_key, kb_collection)
                    st.session_state["summary"] = summary
            else:
                st.session_state["is_valid"] = False
                st.error(f"❌ {validation_msg}")
                st.warning("⚠️ The report was rejected and NOT ingested into the RAG pipeline.")

# عرض التلخيص وزر الحفظ
if st.session_state.get("is_valid") and "summary" in st.session_state:
    st.subheader("3. Physician's Clinical Summary")
    st.markdown(st.session_state["summary"])
    
    st.divider()
    st.subheader("4. Save to Vector Database")
    if st.button("💾 Save Record to ChromaDB"):
        saved_id = pipeline.save_patient_record(
            patient_collection,
            st.session_state["patient_info"],
            st.session_state["report_text"],
            st.session_state["summary"]
        )
        st.success(f"✅ Report & Summary successfully stored in ChromaDB! (Record ID: {saved_id})")
        st.rerun()

    # قسم الشات بوت التفاعلي
    st.divider()
    st.subheader("💬 Chat with Patient Report")
    st.caption("اسأل أي سؤال عن المريض وحالته بناءً على التقرير المرفوع فقط.")

    # عرض الرسائل السابقة
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # إدخال السؤال الجديد
    if user_prompt := st.chat_input("اكتب سؤالك عن تقرير المريض هنا..."):
        # إظهار رسالة المستخدم
        st.session_state["messages"].append({"role": "user", "content": user_prompt})
        with st.chat_message("user"):
            st.write(user_prompt)

        # توليد إجابة الشات بوت
        with st.chat_message("assistant"):
            with st.spinner("جاري التحقق من التقرير..."):
                bot_reply = pipeline.answer_patient_question(
                    report_text=st.session_state["report_text"],
                    user_question=user_prompt,
                    api_key=api_key,
                    chat_history=st.session_state["messages"][:-1]
                )
                st.write(bot_reply)
        st.session_state["messages"].append({"role": "assistant", "content": bot_reply})