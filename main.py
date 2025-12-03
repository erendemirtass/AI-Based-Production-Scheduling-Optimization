import streamlit as st
import pandas as pd
import locale
import logging
import sys
from google.cloud import bigquery
from ortools.sat.python import cp_model
from datetime import date, timedelta, datetime
import plotly.express as px
import vertexai
from vertexai.generative_models import GenerativeModel, Tool, FunctionDeclaration, Content, Part
import traceback
import random
import math
import difflib
import os
import google.auth
from natsort import natsorted
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import uuid

# Sayfa Ayarları
st.set_page_config(layout="wide", page_title="Akıllı Üretim Planlama Platformu")

# --------------------------------------------------------------------------
# --- GÜVENLİK VE KONFİGÜRASYON ---
# --------------------------------------------------------------------------
# Hassas veriler st.secrets üzerinden çekilir
try:
    PROJECT_ID = st.secrets["gcp"]["project_id"]
    LOGIN_PASSWORD = st.secrets["auth"]["login_password"]
    ADMIN_PASSWORD = st.secrets["auth"]["admin_password"]
    EMAIL_SENDER = st.secrets["email"]["sender_address"]
    EMAIL_PASSWORD = st.secrets["email"]["app_password"]
    SMTP_SERVER = st.secrets["email"]["smtp_server"]
    SMTP_PORT = st.secrets["email"]["smtp_port"]
except Exception as e:
    st.error("Konfigürasyon dosyası (.streamlit/secrets.toml) eksik veya hatalı.")
    st.stop()

# BigQuery Tablo Tanımları (Dinamik)
DATASET = "uretim_planlama"
BIGQUERY_GOREVLER_TABLE = f"{PROJECT_ID}.{DATASET}.gorevler"
BIGQUERY_KAYNAKLAR_TABLE = f"{PROJECT_ID}.{DATASET}.kaynaklar"
BIGQUERY_TEZGAHLAR_TABLE = f"{PROJECT_ID}.{DATASET}.tezgahlar"
BIGQUERY_SABLON_ANA_TABLE = f"{PROJECT_ID}.{DATASET}.sablon_ana"
BIGQUERY_SABLONLAR_TABLE = f"{PROJECT_ID}.{DATASET}.uretim_sablonlari"
BIGQUERY_KURALLAR_GRUP_TABLE = f"{PROJECT_ID}.{DATASET}.kurallar_manuel_gruplar"
BIGQUERY_KURALLAR_KISIT_TABLE = f"{PROJECT_ID}.{DATASET}.kurallar_kaynak_kisitlari"
BIGQUERY_KURALLAR_SABIT_BASLANGIC_TABLE = f"{PROJECT_ID}.{DATASET}.kurallar_sabit_baslangic"

BATCHABLE_KAYNAKLAR = ["Satınalma", "Kesimhane", "Tasarım"]

STEP_GROUP_MAPPING = {
    "Hammadde Tedariği": "Genel Satınalma",
    "Platine ve Silindir Malzemeleri Temini": "Genel Satınalma",
    "Malzeme Sipariş": "Genel Satınalma",
    "Kesim Operasyonu": "Genel Kesim",
    "Kesim Süreci": "Genel Kesim",
    "Tasarım Süreci": "Tasarım Çizim",
    "Onay Süreci": "Tasarım Onay"
}

# İlgili Kişiler (Demo veya Genel Yapı)
AMIR_MAIL_LISTESI = {
    "Tasarım": "tasarim_sorumlusu@example.com",
    "Otomasyon": "otomasyon@example.com",
    "Satınalma": "satinalma@example.com",
    "Kesimhane": "kesimhane@example.com",
    "Kaynakhane": "kaynak@example.com",
    "Freze": "freze@example.com",
    "Torna": "torna@example.com",
    "Montaj": "montaj@example.com",
    "Boyahane": "boyahane@example.com"
}

CC_MAIL_LISTESI = [
    "fabrika_muduru@example.com",
    "proje_yonetimi@example.com"
]

# --------------------------------------------------------------------------
# --- GİRİŞ KONTROLÜ ---
# --------------------------------------------------------------------------
def show_login_form():
    st.title("🏭 Akıllı Planlama Platformu")
    st.header("Giriş Ekranı")
    
    password = st.text_input("Lütfen şifreyi girin:", type="password")
    
    if st.button("Giriş Yap"):
        if password == LOGIN_PASSWORD:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("Girdiğiniz şifre yanlış.")
            st.session_state["password_correct"] = False

def is_logged_in():
    return st.session_state.get("password_correct", False)

if not is_logged_in():
    show_login_form()
    st.stop()

# --- YERELLEŞTİRME VE STİL ---
try:
    locale.setlocale(locale.LC_TIME, 'tr_TR.UTF-8')
except locale.Error:
    pass # Sunucuda TR locale yoksa sessizce devam et
        
st.markdown("""
<style>
    [data-testid="stSidebar"] {
        width: 15vw !important;
    }
</style>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------
# --- BAĞLANTI VE AI KURULUMU ---
# --------------------------------------------------------------------------
@st.cache_resource
def init_connections():
    # Öncelik: Streamlit Secrets (Cloud Deploy için)
    # İkincil: Yerel credentials.json dosyası (Geliştirme için)
    credentials = None
    
    # 1. Yöntem: Secrets içinde JSON varsa
    if "gcp_service_account" in st.secrets:
        service_account_info = st.secrets["gcp_service_account"]
        credentials = google.auth.credentials.Credentials.from_service_account_info(service_account_info)
    
    # 2. Yöntem: Yerel dosya varsa
    elif os.path.exists('credentials.json'):
        credentials, _ = google.auth.load_credentials_from_file('credentials.json')

    if credentials:
        bq_client = bigquery.Client(project=PROJECT_ID, credentials=credentials)
        vertexai.init(project=PROJECT_ID, location="europe-west1", credentials=credentials)
    else:
        # Default credentials (eğer ortam değişkenleri ayarlıysa)
        bq_client = bigquery.Client(project=PROJECT_ID)
        vertexai.init(project=PROJECT_ID, location="europe-west1")

    # Gemini AI Araç Tanımları
    a_proje_oncelik_degistir = FunctionDeclaration(name="proje_onceligini_degistir", description="Belirli bir projenin önceliğini günceller. Öncelik 1 en yüksek, 5 en düşüktür.", parameters={"type": "object", "properties": {"proje_adi": {"type": "string"}, "yeni_oncelik": {"type": "integer"}}, "required": ["proje_adi", "yeni_oncelik"]})
    a_plan_onayla = FunctionDeclaration(name="plan_sonucunu_onayla_ve_sun", description="Planın analiz ve iterasyon sürecini sonlandırır.", parameters={"type": "object", "properties": {"onay_yorumu": {"type": "string"}}})
    a_kisit_kaldir = FunctionDeclaration(name="dinamik_kisitlari_kaldir", description="Eklenmiş olan tüm dinamik kaynak kurallarını kaldırır.", parameters={"type": "object", "properties": {"kaynak_adi": {"type": "string"}}})
    a_kaynak_ayarla = FunctionDeclaration(name="kaynak_kullanilabilirlik_ayarla", description="Bir kaynağın belirli bir tarih aralığındaki kapasitesini ayarlar.", parameters={"type": "object", "properties": {"kaynak_adi": {"type": "string"}, "baslangic_tarihi": {"type": "string"}, "bitis_tarihi": {"type": "string"}, "yeni_kapasite": {"type": "number"}}, "required": ["kaynak_adi", "baslangic_tarihi", "bitis_tarihi", "yeni_kapasite"]})
    a_plan_hesapla = FunctionDeclaration(name="plani_hesapla_ve_goster", description="Tüm projeler için en uygun üretim planını hesaplar.", parameters={"type": "object", "properties": {}})
    a_proje_sil = FunctionDeclaration(name="projeyi_sil", description="Bir projeyi ve tüm adımlarını veritabanından siler.", parameters={"type": "object", "properties": {"proje_adi": {"type": "string"}}, "required": ["proje_adi"]})
    a_plan_analiz = FunctionDeclaration(name="plani_analiz_et", description="Mevcut plan hakkında soruları cevaplar.", parameters={"type": "object", "properties": {"soru": {"type": "string"}}, "required": ["soru"]})
    a_plan_guncelle = FunctionDeclaration(name="plani_guncelle_ve_yeniden_hesapla", description="Sahadan gelen bilgilere göre planı günceller.", parameters={"type": "object", "properties": {"adimid": {"type": "string"}, "yeni_durum": {"type": "string"}, "gun_farki": {"type": "number"}}, "required": ["adimid", "yeni_durum"]})
    a_adim_getir = FunctionDeclaration(name="adim_bilgisi_getir", description="Proje ve adım adından adım ID'sini bulur.", parameters={"type": "object", "properties": {"proje_adi": {"type": "string"}, "adim_adi": {"type": "string"}}, "required": ["proje_adi", "adim_adi"]})

    alet_cantasi = Tool(function_declarations=[a_plan_hesapla, a_proje_sil, a_plan_analiz, a_plan_guncelle, a_adim_getir, a_kaynak_ayarla, a_kisit_kaldir, a_proje_oncelik_degistir, a_plan_onayla])
    
    # Model Güvenliği
    model = GenerativeModel(model_name="gemini-2.0-flash", tools=[alet_cantasi])
    return bq_client, model

bq_client, gemini_model = init_connections()

# --------------------------------------------------------------------------
# --- DATABASE YARDIMCI FONKSİYONLAR ---
# --------------------------------------------------------------------------

def get_kaynak_kisitlari_from_bq():
    try:
        query = f"SELECT * FROM `{BIGQUERY_KURALLAR_KISIT_TABLE}`"
        df = bq_client.query(query).to_dataframe()
        df['baslangic_tarihi'] = pd.to_datetime(df['baslangic_tarihi']).dt.strftime('%Y-%m-%d')
        df['bitis_tarihi'] = pd.to_datetime(df['bitis_tarihi']).dt.strftime('%Y-%m-%d')
        return df.to_dict('records')
    except Exception as e:
        if "Not found" in str(e): return []
        st.error(f"DB Read Error: {e}")
        return []

def save_kaynak_kisitlari_to_bq(kisitlar_listesi):
    try:
        if not kisitlar_listesi:
            bq_client.query(f"DELETE FROM `{BIGQUERY_KURALLAR_KISIT_TABLE}` WHERE true").result()
            return True
        df = pd.DataFrame(kisitlar_listesi)
        df['kural_id'] = [str(uuid.uuid4()) for _ in range(len(df))]
        df['baslangic_tarihi'] = pd.to_datetime(df['baslangic_tarihi']).dt.date
        df['bitis_tarihi'] = pd.to_datetime(df['bitis_tarihi']).dt.date
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        bq_client.load_table_from_dataframe(df, BIGQUERY_KURALLAR_KISIT_TABLE, job_config=job_config).result()
        return True
    except Exception as e:
        st.error(f"DB Save Error: {e}")
        return False

def get_sabit_baslangic_kurallari_from_bq():
    try:
        query = f"SELECT * FROM `{BIGQUERY_KURALLAR_SABIT_BASLANGIC_TABLE}`"
        df = bq_client.query(query).to_dataframe()
        df['sabit_baslangic_tarihi'] = pd.to_datetime(df['sabit_baslangic_tarihi']).dt.strftime('%Y-%m-%d')
        return df.to_dict('records')
    except Exception as e:
        if "Not found" in str(e): return []
        return []

def save_sabit_baslangic_kurallari_to_bq(kurallar_listesi):
    try:
        if not kurallar_listesi:
            bq_client.query(f"DELETE FROM `{BIGQUERY_KURALLAR_SABIT_BASLANGIC_TABLE}` WHERE true").result()
            return True
        df = pd.DataFrame(kurallar_listesi)
        if 'kural_id' not in df.columns or df['kural_id'].isnull().any():
             df['kural_id'] = [row.get('kural_id') or str(uuid.uuid4()) for _, row in df.iterrows()]
        df['sabit_baslangic_tarihi'] = pd.to_datetime(df['sabit_baslangic_tarihi']).dt.date
        df['eklenme_tarihi'] = pd.to_datetime(datetime.now())
        bq_columns = ['kural_id', 'adimid', 'projeadi', 'adimadi', 'sabit_baslangic_tarihi', 'eklenme_tarihi']
        df_to_load = df[[col for col in bq_columns if col in df.columns]]
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        bq_client.load_table_from_dataframe(df_to_load, BIGQUERY_KURALLAR_SABIT_BASLANGIC_TABLE, job_config=job_config).result()
        return True
    except Exception as e:
        st.error(f"DB Save Error: {e}")
        return False

def get_manual_groups_from_bq():
    try:
        query = f"SELECT grup_id, adim_id FROM `{BIGQUERY_KURALLAR_GRUP_TABLE}` ORDER BY grup_id"
        df = bq_client.query(query).to_dataframe()
        if df.empty: return []
        return df.groupby('grup_id')['adim_id'].apply(list).tolist()
    except Exception: return []

def save_manual_groups_to_bq(gruplar_listesi):
    try:
        if not gruplar_listesi:
            bq_client.query(f"DELETE FROM `{BIGQUERY_KURALLAR_GRUP_TABLE}` WHERE true").result()
            return True
        rows_to_insert = []
        for group in gruplar_listesi:
            grup_id = str(uuid.uuid4())
            for adim_id in group:
                rows_to_insert.append({'grup_id': grup_id, 'adim_id': adim_id})
        if not rows_to_insert: return True
        df = pd.DataFrame(rows_to_insert)
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        bq_client.load_table_from_dataframe(df, BIGQUERY_KURALLAR_GRUP_TABLE, job_config=job_config).result()
        return True
    except Exception as e:
        st.error(f"DB Save Error: {e}")
        return False

# --------------------------------------------------------------------------
# --- SESSION STATE YÖNETİMİ ---
# --------------------------------------------------------------------------
if 'new_project_steps' not in st.session_state: st.session_state.new_project_steps = []
if 'plan_df' not in st.session_state: st.session_state.plan_df = None
if 'editable_df' not in st.session_state: st.session_state.editable_df = None
if 'chat_messages' not in st.session_state: st.session_state.chat_messages = [{"role": "assistant", "content": "Merhaba! Mevcut planı analiz edebilir veya satış teklifi fizibilitesi yapabilirim."}]
if 'edit_mode' not in st.session_state: st.session_state.edit_mode = False
if 'kaynak_kisitlari' not in st.session_state: st.session_state.kaynak_kisitlari = []
if 'fizibilite_sepeti' not in st.session_state: st.session_state.fizibilite_sepeti = []
if 'toplu_analiz_sonucu' not in st.session_state: st.session_state.toplu_analiz_sonucu = ""
if 'ai_supervisor_report' not in st.session_state: st.session_state.ai_supervisor_report = None
if 'manual_groups' not in st.session_state: st.session_state.manual_groups = []
if 'sabit_baslangic_kurallari' not in st.session_state: st.session_state.sabit_baslangic_kurallari = []
if 'fizibilite_manual_groups' not in st.session_state: st.session_state.fizibilite_manual_groups = []
if 'template_steps_to_load' not in st.session_state: st.session_state.template_steps_to_load = []
if 'current_template_step_index' not in st.session_state: st.session_state.current_template_step_index = 0
if 'admin_password_correct' not in st.session_state: st.session_state.admin_password_correct = False
if 'rules_loaded' not in st.session_state:
    st.session_state.manual_groups = get_manual_groups_from_bq()
    st.session_state.kaynak_kisitlari = get_kaynak_kisitlari_from_bq()
    st.session_state.sabit_baslangic_kurallari = get_sabit_baslangic_kurallari_from_bq()
    st.session_state.rules_loaded = True

# --------------------------------------------------------------------------
# --- DATA FETCHING FONKSİYONLARI ---
# --------------------------------------------------------------------------
@st.cache_data(ttl=300)
def get_all_projects_df(_client: bigquery.Client):
    try:
        query = f"SELECT * FROM `{BIGQUERY_GOREVLER_TABLE}`"
        df = _client.query(query).to_dataframe()
        for col in ['projebaslangictarihi', 'projebitistarihi']:
            if col in df.columns: df[col] = pd.to_datetime(df[col], errors='coerce')
        return df
    except Exception as e:
        return pd.DataFrame()

@st.cache_data(ttl=300)
def get_distinct_values(_client: bigquery.Client, column_name):
    try:
        query = f"SELECT DISTINCT {column_name} FROM `{BIGQUERY_GOREVLER_TABLE}` WHERE {column_name} IS NOT NULL ORDER BY {column_name}"
        df = _client.query(query).to_dataframe()
        return df[column_name].tolist()
    except Exception: return []

@st.cache_data(ttl=300)
def get_kaynaklar_df(_client: bigquery.Client):
    try:
        query = f"SELECT kaynakadi, kapasite FROM `{BIGQUERY_KAYNAKLAR_TABLE}`"
        return _client.query(query).to_dataframe()
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=300)
def get_tezgahlar_df(_client: bigquery.Client):
    try:
        query = f"SELECT tezgahadi, kaynakadi FROM `{BIGQUERY_TEZGAHLAR_TABLE}`"
        return _client.query(query).to_dataframe()
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=60)
def get_sablon_ana_df(_client: bigquery.Client):
    try:
        query = f"SELECT * FROM `{BIGQUERY_SABLON_ANA_TABLE}`"
        return _client.query(query).to_dataframe()
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=60)
def get_uretim_sablonlari_df(_client: bigquery.Client):
    try:
        query = f"SELECT * FROM `{BIGQUERY_SABLONLAR_TABLE}`"
        return _client.query(query).to_dataframe()
    except Exception: return pd.DataFrame()

# --------------------------------------------------------------------------
# --- TEMEL İŞLEV FONKSİYONLARI ---
# --------------------------------------------------------------------------

def add_step_callback():
    adim_adi_val = st.session_state.get("new_adim_adi", "").strip() if st.session_state.get("selected_adim_adi") == "**Yeni Ekle...**" else st.session_state.get("selected_adim_adi")
    selected_kaynak_adi = st.session_state.get("new_kaynak_adi", "").strip() if st.session_state.get("selected_kaynak_adi") == "**Yeni Ekle...**" else st.session_state.get("selected_kaynak_adi")

    if not adim_adi_val or not selected_kaynak_adi:
        st.warning("Adım Adı ve Birim/Kaynak alanları boş bırakılamaz.")
        return

    tezgah_val = None
    if selected_kaynak_adi == "Freze":
        selected_tezgahlar = st.session_state.get("selected_tezgah_multi", [])
        if selected_tezgahlar:
            tezgah_val = ",".join(selected_tezgahlar)
    else:
        selected_tezgah = st.session_state.get("new_tezgah", "").strip() if st.session_state.get("selected_tezgah_single") == "**Yeni Ekle...**" else st.session_state.get("selected_tezgah_single")
        if selected_tezgah not in ["-- Boş Bırak --", None, ""]:
            tezgah_val = selected_tezgah
    
    onceki_adımlar_listesi = st.session_state.onceki_adim_input
    
    st.session_state.new_project_steps.append({
        "AdimAdi": adim_adi_val,
        "kaynakadi": selected_kaynak_adi,
        "tezgahadi": tezgah_val,
        "suregun": st.session_state.sure_gun_input,
        "toleransgun": st.session_state.tolerans_gun_input,
        "OncekiAdimAdi": onceki_adımlar_listesi
    })

    if st.session_state.template_steps_to_load:
        next_index = st.session_state.current_template_step_index + 1
        if next_index < len(st.session_state.template_steps_to_load):
            st.session_state.current_template_step_index = next_index
        else:
            clear_template_loading_state()
            st.success("Tüm adımlar eklendi!")

def dinamik_kisitlari_kaldir(kaynak_adi: str = None):
    if 'kaynak_kisitlari' not in st.session_state or not st.session_state.kaynak_kisitlari:
        return "Aktif kural yok."
    
    if kaynak_adi:
        st.session_state.kaynak_kisitlari = [k for k in st.session_state.kaynak_kisitlari if k.get('kaynak_adi', '').lower() != kaynak_adi.lower()]
    else:
        st.session_state.kaynak_kisitlari.clear()

    if save_kaynak_kisitlari_to_bq(st.session_state.kaynak_kisitlari):
         return plani_hesapla_ve_goster()
    else:
        return "DB Hatası."

def kaynak_kullanilabilirlik_ayarla(kaynak_adi: str, baslangic_tarihi: str, bitis_tarihi: str, yeni_kapasite: int):
    yeni_kisit = {"kaynak_adi": kaynak_adi, "baslangic_tarihi": baslangic_tarihi, "bitis_tarihi": bitis_tarihi, "yeni_kapasite": yeni_kapasite}
    if yeni_kisit not in st.session_state.get('kaynak_kisitlari', []):
        st.session_state.kaynak_kisitlari.append(yeni_kisit)
        if not save_kaynak_kisitlari_to_bq(st.session_state.kaynak_kisitlari):
            st.session_state.kaynak_kisitlari.pop()
            return "DB Kayıt Hatası."
    return plani_hesapla_ve_goster()

def clear_template_loading_state():
    st.session_state.template_steps_to_load = []
    st.session_state.current_template_step_index = 0

def haftalik_raporlari_olustur_ve_gonder(test_email=None):
    try:
        is_test = bool(test_email)
        mode = "TEST" if is_test else "GERÇEK"
        
        df_gorevler = get_all_projects_df(bq_client)
        df_kaynaklar = get_kaynaklar_df(bq_client)
        df_tezgahlar = get_tezgahlar_df(bq_client)
        
        plan_df, _, _ = hesapla_ve_optimize_et(
            df_gorevler, df_kaynaklar, df_tezgahlar,
            manual_start_groups=st.session_state.get('manual_groups', []),
            sabit_baslangic_kurallari=st.session_state.get('sabit_baslangic_kurallari', [])
        )

        if plan_df is None or plan_df.empty:
            return "Plan hesaplanamadı."

        bugun = pd.to_datetime(date.today())
        hafta_basi = bugun.floor('D') - pd.to_timedelta(bugun.weekday(), unit='D')
        hafta_sonu = hafta_basi + pd.to_timedelta(6, unit='D')
        
        gantt_end_range = bugun + pd.to_timedelta(21, unit='D')
        
        for birim, amir_mail in AMIR_MAIL_LISTESI.items():
            # (Raporlama mantığı ve email gönderme kodu buraya gelecek - kod kısaltıldı)
            # Not: smtp.login kısmında EMAIL_SENDER ve EMAIL_PASSWORD değişkenlerini kullanın.
            pass
            
        return f"{mode} raporları gönderildi."
    except Exception as e:
        return f"Hata: {e}"

# --------------------------------------------------------------------------
# --- OPTİMİZASYON VE AI FONKSİYONLARI ---
# --------------------------------------------------------------------------

def yapay_zeka_denetiminde_plan_olustur():
    st.session_state.ai_supervisor_report = None
    MAX_ATTEMPTS = 5
    
    with st.status("🤖 AI Süpervizör Devrede...", expanded=True) as status:
        try:
            status.write("Veriler analiz ediliyor...")
            gorevler_df = get_all_projects_df(bq_client)
            if gorevler_df.empty:
                status.update(label="Veri Hatası", state="error")
                return

            proje_hedefleri = gorevler_df[['projeadi', 'projebitistarihi', 'proje_onceligi']].drop_duplicates().to_string()
            chat = gemini_model.start_chat()
            
            plani_hesapla_ve_goster()
            
            for i in range(MAX_ATTEMPTS):
                attempt = i + 1
                status.write(f"**DENEME {attempt}/{MAX_ATTEMPTS}:** Plan analiz ediliyor...")
                
                mevcut_plan_df = st.session_state.plan_df
                plan_ozeti_csv = mevcut_plan_df.to_csv()
                
                prompt = f"""
                Sen bir üretim planlama süpervizörüsün.
                HEDEFLER:\n{proje_hedefleri}\n\nMEVCUT PLAN:\n{plan_ozeti_csv}
                Görevlerin:
                1. Gecikmeleri analiz et.
                2. Gerekirse 'proje_onceligini_degistir' ile müdahale et.
                3. Sonuç mükemmelse 'plan_sonucunu_onayla_ve_sun' çağır.
                """
                
                response = chat.send_message(prompt)
                # ... (AI Yanıt İşleme Kodu - Orijinal koddaki gibi)
                
        except Exception as e:
            st.error(f"AI Hatası: {e}")

def hesapla_ve_optimize_et(df_input: pd.DataFrame, kaynaklar_df_full: pd.DataFrame, tezgahlar_df_full: pd.DataFrame, simulasyon_modu=False, manual_start_groups=None, sabit_baslangic_kurallari=None):
    """
    Google OR-Tools Optimizasyon Motoru
    """
    try:
        df = df_input.copy()
        # ... (Veri temizleme adımları)
        
        model = cp_model.CpModel()
        # ... (Model kurma, kısıt ekleme, OR-Tools mantığı)
        # Orijinal koddaki optimizasyon mantığının aynısı buraya gelecek.
        
        # Basitleştirilmiş dönüş (Placeholder):
        return pd.DataFrame(), 0, "OPTIMAL" 
    except Exception as e:
        return None, 0, None

def create_enhanced_gantt_chart(plan_df, editable_df=None, date_range_start=None, date_range_end=None):
    # Plotly Gantt Chart oluşturma kodu
    if plan_df is None or plan_df.empty:
        return px.timeline(title="Veri Yok")
    
    fig = px.timeline(plan_df, x_start="Başlangıç", x_end="Bitiş", y="Proje Adı", color="Kaynak")
    return fig

# --------------------------------------------------------------------------
# --- ARAYÜZ (STREAMLIT) ---
# --------------------------------------------------------------------------

# Yan Menü (Chatbot)
with st.sidebar:
    st.header("🤖 Asistan")
    chat_container = st.container()
    if prompt := st.chat_input("Komut girin..."):
        # Chat işleme mantığı
        pass

# Ana Sekmeler
tab1, tab_fizibilite, tab2, tab3, tab4 = st.tabs(["📊 Ana Panel", "📈 Fizibilite", "Planlama", "➕ Proje Ekle", "⚙️ Yönetim"])

with tab1:
    st.header("📊 Üretim Planlama Paneli")
    # ... (Dashboard kodları)

with tab_fizibilite:
    st.header("📈 Satış Fizibilite")
    # ... (Fizibilite kodları)

with tab2:
    st.header("Planlama Adımları")
    # ... (Detaylı planlama kodları)

with tab3:
    st.header("➕ Yeni Proje Ekle")
    # ... (Proje ekleme formları)

with tab4:
    def show_admin_login():
        st.header("⚙️ Yönetim Girişi")
        p = st.text_input("Şifre:", type="password")
        if st.button("Gir"):
            if p == ADMIN_PASSWORD:
                st.session_state.admin_password_correct = True
                st.rerun()
    
    if not st.session_state.get("admin_password_correct"):
        show_admin_login()
    else:
        st.header("⚙️ Yönetim Paneli")
        # ... (Yönetim fonksiyonları)

# Not: Bu kod, orijinal kodun temizlenmiş iskeletidir.
