import streamlit as st
import io
import json
import fitz  # PyMuPDF
import unicodedata
import re
import random
from supabase import create_client, Client
import google.generativeai as genai
import typing_extensions as typing

from pydantic import BaseModel, Field

# --- TIPOS DE DATOS PARA GEMINI ---
class Pregunta(BaseModel):
    id: int = Field(description="Número de la pregunta")
    enunciado: str = Field(description="El texto principal de la pregunta a responder")
    opciones: list[str] = Field(description="Lista de 4 posibles opciones de respuesta")
    correcta: int = Field(description="Índice (0 a 3) de la respuesta correcta")
    justificacion: str = Field(description="Justificación detallada que cite expresamente el artículo, inciso, numeral, literal, o datos de jurisprudencia (radicado, fecha, sala, magistrado ponente).")
    mapa_mental: str = Field(description="Esquema conceptual horizontal usando flechas (->)")
    refran: str = Field(description="Una rima corta, graciosa y coloquial (máximo dos líneas) que resuma la regla jurídica de fondo. Debe tener una métrica muy marcada y sonora (idealmente con acentos rítmicos fuertes en las sílabas 1, 4, 7 y 10) para que funcione como una regla mnemotécnica cómica y fácil de recordar en un examen.")

class TestResult(BaseModel):
    preguntas: list[Pregunta] = Field(description="Lista con exactamente 5 preguntas generadas")

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Estudio Concursos", page_icon="📚", layout="wide")

# --- TEMA VISUAL: PIZARRÓN + MADERA ---
import os
import base64
_css_path = os.path.join(os.path.dirname(__file__), "static", "style.css")
if os.path.exists(_css_path):
    with open(_css_path, encoding="utf-8") as _f:
        st.markdown(f"<style>{_f.read()}</style>", unsafe_allow_html=True)

# Inyectar imagen de fondo
_img_path = os.path.join(os.path.dirname(__file__), "static", "pizarron.png")
if os.path.exists(_img_path):
    with open(_img_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    bg_css = f"""
    <style>
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
        background-image: url("data:image/png;base64,{encoded_string}") !important;
        background-size: 100% 100% !important;
        background-position: center !important;
        background-attachment: fixed !important;
        background-repeat: no-repeat !important;
    }}
    </style>
    """
    st.markdown(bg_css, unsafe_allow_html=True)

# --- INICIALIZACIÓN DE SESSION STATE ---
if 'progreso_por_doc' not in st.session_state:
    st.session_state.progreso_por_doc = {} 
if 'estadisticas' not in st.session_state:
    st.session_state.estadisticas = {'total': 0, 'correctas': 0, 'incorrectas': 0}
if 'test_actual' not in st.session_state:
    st.session_state.test_actual = None
if 'respuestas_usuario' not in st.session_state:
    st.session_state.respuestas_usuario = {}
if 'sesion_activa' not in st.session_state:
    st.session_state.sesion_activa = None
if 'concurso_activo' not in st.session_state:
    st.session_state.concurso_activo = None
if 'doc_activo' not in st.session_state:
    st.session_state.doc_activo = None

# --- CONEXIÓN A SUPABASE ---
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Error al conectar a Supabase. Verifica tus secrets: {e}")
        st.stop()

supabase = init_supabase()
BUCKET_NAME = 'documentos_concursos'

# --- CONFIGURACIÓN DE GEMINI ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    # El modelo se instanciará dinámicamente en la barra lateral
except Exception as e:
    st.error(f"Error al configurar la API de Gemini: {e}")
    st.stop()

# --- FUNCIONES DE ALMACENAMIENTO Y CONCURSOS ---
@st.cache_data(show_spinner=False)
def descargar_documento_cache(ruta):
    return supabase.storage.from_(BUCKET_NAME).download(ruta)

def obtener_concursos():
    try:
        res = descargar_documento_cache('concursos.json')
        return json.loads(res.decode('utf-8'))
    except Exception:
        return []

def guardar_concursos(lista_concursos):
    datos = json.dumps(lista_concursos).encode('utf-8')
    try:
        supabase.storage.from_(BUCKET_NAME).update(file=datos, path='concursos.json', file_options={"content-type": "application/json", "cache-control": "no-cache"})
        descargar_documento_cache.clear()
        return True
    except Exception:
        try:
            supabase.storage.from_(BUCKET_NAME).upload(file=datos, path='concursos.json', file_options={"content-type": "application/json", "cache-control": "no-cache"})
            descargar_documento_cache.clear()
            return True
        except Exception as e:
            st.error(f"Error de permisos en Supabase al guardar: {e}")
            return False

def obtener_sesiones(concurso):
    ruta = f"{safe_name(concurso)}/sesiones.json"
    try:
        res = descargar_documento_cache(ruta)
        return json.loads(res.decode('utf-8'))
    except Exception:
        return {}

def guardar_sesiones(concurso, sesiones_dict):
    ruta = f"{safe_name(concurso)}/sesiones.json"
    datos = json.dumps(sesiones_dict).encode('utf-8')
    try:
        supabase.storage.from_(BUCKET_NAME).update(file=datos, path=ruta, file_options={"content-type": "application/json", "cache-control": "no-cache"})
    except Exception:
        try:
            supabase.storage.from_(BUCKET_NAME).upload(file=datos, path=ruta, file_options={"content-type": "application/json", "cache-control": "no-cache"})
        except Exception:
            pass
    descargar_documento_cache.clear()

def crear_concurso(nombre):
    concursos = obtener_concursos()
    if nombre not in concursos:
        concursos.append(nombre)
        return guardar_concursos(concursos)
    return True

def safe_name(name):
    """Limpia tildes, espacios y caracteres especiales para usarlos en rutas de Supabase."""
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
    name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
    return name

def eliminar_concurso(concurso):
    docs = listar_documentos(concurso)
    if docs:
        rutas = [f"{safe_name(concurso)}/{d}" for d in docs]
        try:
            supabase.storage.from_(BUCKET_NAME).remove(rutas)
        except Exception as e:
            st.error(f"Error al vaciar los archivos del concurso: {e}")
            
    concursos = obtener_concursos()
    if concurso in concursos:
        concursos.remove(concurso)
        guardar_concursos(concursos)

def listar_documentos(concurso):
    try:
        res = supabase.storage.from_(BUCKET_NAME).list(path=safe_name(concurso))
        archivos = [f['name'] for f in res if f['name'].lower().endswith(('.pdf', '.txt'))]
        return archivos
    except Exception as e:
        return []

def subir_documento(file, concurso):
    try:
        file_bytes = file.getvalue()
        ruta = f"{safe_name(concurso)}/{safe_name(file.name)}"
        supabase.storage.from_(BUCKET_NAME).upload(file=file_bytes, path=ruta, file_options={"content-type": file.type})
        return True
    except Exception as e:
        st.sidebar.error(f"Error al subir el archivo (quizás ya existe): {e}")
        return False

def eliminar_documento(concurso, doc_name):
    ruta = f"{safe_name(concurso)}/{doc_name}"
    try:
        supabase.storage.from_(BUCKET_NAME).remove([ruta])
        st.sidebar.warning(f"Se ha eliminado el archivo '{doc_name}'.")
    except Exception as e:
        st.sidebar.error(f"Error al eliminar el archivo: {e}")

@st.cache_data(show_spinner=False)
def extraer_texto_doc_cached(concurso, doc_name):
    ruta = f"{safe_name(concurso)}/{doc_name}"
    try:
        res = descargar_documento_cache(ruta)
        if doc_name.endswith('.pdf'):
            pdf_document = fitz.open(stream=res, filetype="pdf")
            texto = ""
            for page in pdf_document:
                extracted = page.get_text()
                if extracted:
                    texto += extracted + "\n"
            pdf_document.close()
            return texto
        elif doc_name.endswith('.txt'):
            return res.decode('utf-8')
    except Exception as e:
        return None

def extraer_texto_doc(concurso, doc_name):
    """Descarga el documento a la memoria y extrae su texto."""
    return extraer_texto_doc_cached(concurso, doc_name)

# --- INTERFAZ BARRA LATERAL ---
with st.sidebar:
    st.header("🤖 Configuración de IA")
    modelo_seleccionado = st.selectbox(
        "Modelo de Inteligencia Artificial:",
        options=["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
        format_func=lambda x: f"🧠 3.1 Pro (Máxima Complejidad)" if "3.1-pro" in x else f"⚡ 3.5 Flash (Avanzado)" if "3.5-flash" in x else f"⚡ 1.5 Flash (Básico)" if "1.5-flash" in x else "🧠 1.5 Pro (Clásico)",
        index=0
    )
    
    try:
        model = genai.GenerativeModel(modelo_seleccionado)
    except Exception as e:
        st.error(f"Error instanciando modelo: {e}")

    st.divider()

    st.header("✨ Crear Nuevo Concurso")
    nuevo_concurso = st.text_input("Nombre del Concurso")
    if st.button("Guardar concurso"):
        if nuevo_concurso.strip():
            if crear_concurso(nuevo_concurso.strip()):
                st.success("Concurso creado exitosamente.")
                import time
                time.sleep(1.0)
                st.rerun()
        else:
            st.warning("Escribe un nombre válido.")
            
    st.divider()
    
    st.header("🗂️ Mis Concursos")
    concursos_existentes = obtener_concursos()
    
    if not concursos_existentes:
        st.info("No hay concursos. Crea el primero arriba.")
        
    for conc in concursos_existentes:
        with st.expander(f"🏛️ {conc}"):
            # 1. Fuentes (minimized)
            documentos = listar_documentos(conc)
            st.caption(f"📚 {len(documentos)} fuentes subidas")
            
            with st.expander("⚙️ Administrar Fuentes"):
                uploaded_file = st.file_uploader("Sube un temario (PDF/TXT)", type=["pdf", "txt"], key=f"up_{conc}")
                if uploaded_file is not None:
                    if st.button("Guardar documento", key=f"btn_up_{conc}"):
                        if subir_documento(uploaded_file, conc):
                            st.success("Guardado.")
                            import time
                            time.sleep(1.0)
                            st.rerun()
                
                if documentos:
                    st.write("**Fuentes Actuales:**")
                    for doc in documentos:
                        col1, col2 = st.columns([4, 1])
                        display_doc = doc if len(doc) <= 40 else doc[:25] + "..." + doc[-10:]
                        col1.write(f"📄 {display_doc}")
                        if col2.button("🗑️", key=f"del_{conc}_{doc}", help="Eliminar"):
                            eliminar_documento(conc, doc)
                            st.rerun()
            
            st.divider()
            
            # 2. Sesiones
            st.write("🧠 **Sesiones de Estudio**")
            sesiones = obtener_sesiones(conc)
            nombres_sesiones = list(sesiones.keys())
            
            opciones_sesiones = ["-- Crear Nueva --"] + nombres_sesiones
            sesion_elegida = st.selectbox("Cargar sesión:", opciones_sesiones, key=f"ses_{conc}")
            
            if sesion_elegida == "-- Crear Nueva --":
                nueva_sesion = st.text_input("Nombre de la nueva sesión:", key=f"nses_{conc}")
                if st.button("Crear Sesión", key=f"btn_nses_{conc}"):
                    if nueva_sesion.strip():
                        sesiones[nueva_sesion.strip()] = {"progreso_por_doc": {}}
                        guardar_sesiones(conc, sesiones)
                        st.success("Sesión creada.")
                        st.rerun()
                    else:
                        st.warning("Escribe un nombre válido.")
            else:
                if st.button(f"🗑️ Eliminar '{sesion_elegida}'", key=f"delses_{conc}"):
                    del sesiones[sesion_elegida]
                    guardar_sesiones(conc, sesiones)
                    if st.session_state.sesion_activa == sesion_elegida and st.session_state.concurso_activo == conc:
                        st.session_state.sesion_activa = None
                        st.session_state.concurso_activo = None
                        st.session_state.doc_activo = None
                    st.rerun()
                    
            st.divider()
            
            # 3. Documento a estudiar y Acción
            st.write("📖 **Documento a estudiar**")
            if documentos:
                opciones_doc = ["📚 Todas las fuentes (Estudio Global)"] + documentos
                doc_seleccionado_ui = st.selectbox("Selecciona fuente:", opciones_doc, key=f"doc_{conc}")
                puede_estudiar = sesion_elegida != "-- Crear Nueva --"
                
                if st.button("▶️ Seleccionar para estudiar", type="primary", key=f"start_{conc}", disabled=not puede_estudiar):
                    st.session_state.concurso_activo = conc
                    st.session_state.sesion_activa = sesion_elegida
                    st.session_state.doc_activo = doc_seleccionado_ui
                    st.session_state.progreso_por_doc = sesiones[sesion_elegida].get("progreso_por_doc", {})
                    st.rerun()
            else:
                st.info("Sube una fuente primero.")
                
            st.divider()
            if st.button(f"⚠️ Eliminar Concurso", key=f"delconc_{conc}"):
                eliminar_concurso(conc)
                if st.session_state.concurso_activo == conc:
                    st.session_state.concurso_activo = None
                    st.session_state.doc_activo = None
                    st.session_state.sesion_activa = None
                st.rerun()

# --- LÓGICA PRINCIPAL ---
st.title("📚 Creador de Tests para Concursos")

cols_stats = st.columns(3)
cols_stats[0].metric("Preguntas Respondidas", st.session_state.estadisticas['total'])
porcentaje_aciertos = 0
porcentaje_errores = 0
if st.session_state.estadisticas['total'] > 0:
    porcentaje_aciertos = (st.session_state.estadisticas['correctas'] / st.session_state.estadisticas['total']) * 100
    porcentaje_errores = (st.session_state.estadisticas['incorrectas'] / st.session_state.estadisticas['total']) * 100

cols_stats[1].metric("% Aciertos", f"{porcentaje_aciertos:.1f}%")
cols_stats[2].metric("% Errores", f"{porcentaje_errores:.1f}%")
st.divider()

concurso_main = st.session_state.concurso_activo
doc_main = st.session_state.doc_activo
sesion_main = st.session_state.sesion_activa

if concurso_main and doc_main and sesion_main:
    id_progreso = f"{concurso_main}/{doc_main}"
    
    st.subheader(f"🏛️ Concurso: {concurso_main}")
    st.write(f"**Sesión Activa:** {sesion_main} | **Estudiando:** {doc_main}")
    
    docs_globales = listar_documentos(concurso_main) if doc_main == "📚 Todas las fuentes (Estudio Global)" else []
    
    if doc_main == "📚 Todas las fuentes (Estudio Global)":
        if docs_globales:
            suma_prog = 0.0
            for d in docs_globales:
                k = f"{concurso_main}/{d}"
                if k not in st.session_state.progreso_por_doc:
                    st.session_state.progreso_por_doc[k] = 0.0
                suma_prog += st.session_state.progreso_por_doc[k]
            progreso_actual = suma_prog / len(docs_globales)
        else:
            progreso_actual = 0.0
    else:
        if id_progreso not in st.session_state.progreso_por_doc:
            st.session_state.progreso_por_doc[id_progreso] = 0.0
        progreso_actual = st.session_state.progreso_por_doc[id_progreso]
    
    col_prog1, col_prog2 = st.columns([4, 1])
    with col_prog1:
        st.progress(progreso_actual / 100.0, text=f"Progreso de lectura acumulado: {progreso_actual:.1f}%")
    with col_prog2:
        if st.button("🔄 Reiniciar"):
            st.session_state.confirmar_reinicio = True
            st.rerun()

    if st.session_state.get("confirmar_reinicio", False):
        st.warning("⚠️ ¿Seguro que deseas reiniciar el progreso de estudio? Esta acción pondrá tu avance en 0% y no se puede deshacer.")
        col_conf1, col_conf2 = st.columns(2)
        if col_conf1.button("✅ Sí, reiniciar", type="primary"):
            if doc_main == "📚 Todas las fuentes (Estudio Global)":
                for d in docs_globales:
                    st.session_state.progreso_por_doc[f"{concurso_main}/{d}"] = 0.0
            else:
                st.session_state.progreso_por_doc[id_progreso] = 0.0
            sesiones = obtener_sesiones(concurso_main)
            if st.session_state.sesion_activa in sesiones:
                sesiones[st.session_state.sesion_activa]["progreso_por_doc"] = st.session_state.progreso_por_doc
                guardar_sesiones(concurso_main, sesiones)
            st.session_state.confirmar_reinicio = False
            st.rerun()
        if col_conf2.button("❌ Cancelar"):
            st.session_state.confirmar_reinicio = False
            st.rerun()

    with st.expander("📊 Ver progreso por fuente", expanded=False):
        docs_para_mostrar = docs_globales if doc_main == "📚 Todas las fuentes (Estudio Global)" else listar_documentos(concurso_main)
        if docs_para_mostrar:
            for d in docs_para_mostrar:
                k = f"{concurso_main}/{d}"
                prog = st.session_state.progreso_por_doc.get(k, 0.0)
                st.progress(prog / 100.0, text=f"{d}: {prog:.1f}%")
        else:
            st.info("Aún no hay fuentes subidas a este concurso.")

    paginas_estudio = st.slider("¿Cuántas páginas (aprox.) deseas abarcar en el próximo test?", min_value=1, max_value=30, value=3, step=1)
    
    col_btn1, col_btn2 = st.columns(2)
    generar_avance = col_btn1.button("🚀 Avanzar materia nueva", type="primary", use_container_width=True)
    generar_repaso = col_btn2.button("🔄 Repasar lo ya estudiado", use_container_width=True)

    if generar_avance or generar_repaso:
        es_repaso = generar_repaso
        if es_repaso and progreso_actual == 0.0:
            st.warning("Aún no tienes progreso en este documento para repasar. Haz clic en 'Avanzar materia nueva' primero.")
        else:
            with st.status("Preparando tu test...", expanded=True) as status:
                st.write("📖 Extrayendo texto del documento...")
                texto_seccion = ""
                doc_original_name = doc_main
                instruccion_extra = ""
                doc_a_avanzar = None
                avance_pct_doc = 0.0
                
                if doc_main == "📚 Todas las fuentes (Estudio Global)":
                    st.write("📚 Analizando fuentes del concurso...")
                    docs_del_concurso = listar_documentos(concurso_main)
                    if not docs_del_concurso:
                        status.update(label="Error", state="error")
                        st.error("No hay documentos en este concurso.")
                        st.stop()
                    
                    txt = None
                    doc_elegido = None
                    prog_doc = 0.0
                    
                    if es_repaso:
                        docs_repaso = [d for d in docs_del_concurso if st.session_state.progreso_por_doc.get(f"{concurso_main}/{d}", 0.0) > 0.0]
                        if not docs_repaso:
                            status.update(label="Error", state="error", expanded=True)
                            st.warning("Aún no tienes progreso en ninguna fuente para repasar.")
                            st.stop()
                        random.shuffle(docs_repaso)
                        for d in docs_repaso:
                            temp_txt = extraer_texto_doc_cached(concurso_main, d)
                            if temp_txt and temp_txt.strip():
                                txt = temp_txt
                                doc_elegido = d
                                prog_doc = st.session_state.progreso_por_doc.get(f"{concurso_main}/{d}", 0.0)
                                break
                    else:
                        docs_pendientes = [(d, st.session_state.progreso_por_doc.get(f"{concurso_main}/{d}", 0.0)) for d in docs_del_concurso if st.session_state.progreso_por_doc.get(f"{concurso_main}/{d}", 0.0) < 100.0]
                        if not docs_pendientes:
                            status.update(label="Error", state="error", expanded=True)
                            st.warning("Ya has completado todas las fuentes. Reinicia tu progreso para empezar de nuevo.")
                            st.stop()
                        
                        docs_pendientes.sort(key=lambda x: x[1])
                        candidatos = [d for d in docs_pendientes if d[0] != st.session_state.get("last_global_doc")]
                        if not candidatos:
                            candidatos = docs_pendientes

                        for d, p in candidatos:
                            temp_txt = extraer_texto_doc_cached(concurso_main, d)
                            if temp_txt and temp_txt.strip():
                                txt = temp_txt
                                doc_elegido = d
                                prog_doc = p
                                st.session_state["last_global_doc"] = d
                                break
                            else:
                                k = f"{concurso_main}/{d}"
                                st.session_state.progreso_por_doc[k] = 100.0
                                st.warning(f"⚠️ El documento '{d}' no contiene texto extraíble. Se ha omitido.")
                                
                    if not txt or not doc_elegido:
                        status.update(label="Error", state="error")
                        st.error("No se pudo leer ninguno de los documentos (pueden ser PDFs escaneados).")
                        st.stop()
                        
                    largo = len(txt)
                    chars_per_doc = paginas_estudio * 1800
                    
                    if es_repaso:
                        fin_idx_repaso = int((prog_doc / 100.0) * largo)
                        tamaño_chunk = min(chars_per_doc, largo)
                        max_inicio = max(0, fin_idx_repaso - tamaño_chunk)
                        inicio_idx = random.randint(0, max_inicio)
                        fin_idx = min(inicio_idx + tamaño_chunk, fin_idx_repaso)
                    else:
                        inicio_idx = int((prog_doc / 100.0) * largo)
                        tamaño_chunk = min(chars_per_doc, largo)
                        fin_idx = min(inicio_idx + tamaño_chunk, largo)
                        
                        doc_a_avanzar = doc_elegido
                        avance_pct_doc = (tamaño_chunk / largo) * 100.0 if largo > 0 else 100.0
                        
                    chunk = txt[inicio_idx:fin_idx]
                    if chunk.strip():
                        texto_seccion = f"\n\n--- DOCUMENTO: {doc_elegido} ---\n{chunk}"
                    else:
                        texto_seccion = ""
                    
                    doc_original_name = doc_elegido
                    instruccion_extra = ""
                    if es_repaso:
                        instruccion_extra = "\\n\\nIMPORTANTE: ESTE ES UN TEST DE REPASO. Elabora preguntas diferentes a las clásicas para afianzar la memoria."
                else:
                    texto_completo = extraer_texto_doc_cached(concurso_main, doc_main)
                    if texto_completo:
                        total_chars = len(texto_completo)
                        
                        total_paginas_estimadas = max(1, total_chars / 1800)
                        porcentaje_estudio = (paginas_estudio / total_paginas_estimadas) * 100.0
                        
                        if es_repaso:
                            fin_idx_repaso = int((progreso_actual / 100.0) * total_chars)
                            tamaño_chunk = int((porcentaje_estudio / 100.0) * total_chars)
                            max_inicio = max(0, fin_idx_repaso - tamaño_chunk)
                            inicio_idx = random.randint(0, max_inicio)
                            fin_idx = min(inicio_idx + tamaño_chunk, fin_idx_repaso)
                            instruccion_extra = "\\n\\nIMPORTANTE: ESTE ES UN TEST DE REPASO. Elabora preguntas diferentes a las clásicas para afianzar la memoria."
                        else:
                            inicio_idx = int((progreso_actual / 100.0) * total_chars)
                            fin_idx = int(((progreso_actual + porcentaje_estudio) / 100.0) * total_chars)
                            fin_idx = min(fin_idx, total_chars)
                            instruccion_extra = ""
                            
                        chunk = texto_completo[inicio_idx:fin_idx]
                        if chunk.strip():
                            texto_seccion = f"\n\n--- DOCUMENTO: {doc_main} ---\n{chunk}"
                        else:
                            texto_seccion = ""

                if not texto_seccion.strip():
                    status.update(label="Error de progreso", state="error", expanded=True)
                    st.warning("Ya has completado este documento o la sección no contiene texto. Reinicia tu progreso para empezar de nuevo.")
                else:
                    st.write("🤖 Analizando el texto y estructurando preguntas con IA...")
                    prompt = f"""
                    Actúa como un experto en la creación de exámenes para concursos de méritos públicos.
                    A continuación te proporciono un extracto del documento o documentos a estudiar (si hay varios, cada uno estará separado e identificado):
                    
                    --- INICIO DEL EXTRACTO ---
                    {texto_seccion}
                    --- FIN DEL EXTRACTO ---
                    
                    INSTRUCCIONES ESTRICTAS DE FORMATO:{instruccion_extra}
                    1. Genera EXACTAMENTE 5 preguntas basadas en este texto.
                    2. REGLA OBLIGATORIA: Cada pregunta DEBE tener las claves 'enunciado', 'justificacion', 'mapa_mental' y 'refran'.
                    3. La "justificacion" debe ser detallada y pedagógica. Explica ampliamente por qué la opción es correcta. DEBES citar la fuente con precisión: si el texto hace referencia a leyes, normas, decretos o jurisprudencia, DEBES mencionar su nombre completo sin recortarlo (incluyendo número, año, artículo, inciso, radicado, fecha, sala o ponente). También DEBES hacer referencia explícita al nombre del documento al que pertenece el fragmento (indicado como "--- DOCUMENTO: nombre ---").
                        4. El "mapa_mental" debe ser un esquema conceptual horizontal corto y directo usando flechas. (Ejemplo: Concepto -> Propiedad -> Detalle).
                        5. El "refran" debe ser una rima corta, graciosa y coloquial (máximo dos líneas) que resuma la regla jurídica de fondo. Debe tener una métrica muy marcada y sonora (idealmente con acentos rítmicos fuertes en las sílabas 1, 4, 7 y 10) para que funcione como una regla mnemotécnica cómica y fácil de recordar en un examen.
                        6. REGLA DE SEGURIDAD CRÍTICA: Todo el contenido (incluyendo la justificación) debe ser 100% PARAFRASEADO usando tus propias palabras para evitar filtros de Copyright. Menciona los números de artículos, el nombre de las leyes y radicados, pero NUNCA copies el texto legal ni los extractos normativos de forma literal.
                        7. Devuelve ÚNICAMENTE un JSON válido con la siguiente estructura exacta:
                        {{
                          "preguntas": [
                            {{
                              "id": 1,
                              "enunciado": "¿Pregunta de ejemplo?",
                              "opciones": ["Opción A", "Opción B", "Opción C", "Opción D"],
                              "correcta": 0,
                              "justificacion": "Explicación detallada...",
                              "mapa_mental": "A -> B -> C",
                              "refran": "Si la ley es muy pesada, con la rima es pan comido..."
                            }}
                          ]
                        }}
                        """
                        
                    try:
                        respuesta = model.generate_content(
                            prompt,
                            generation_config=genai.GenerationConfig(
                                response_mime_type="application/json",
                                temperature=0.2,
                                max_output_tokens=8192
                            )
                        )
                        try:
                            json_texto = respuesta.text
                            st.session_state.test_actual = json.loads(json_texto)
                        except Exception as parse_error:
                            finish_reason = respuesta.candidates[0].finish_reason if respuesta.candidates else "Desconocida"
                            long_texto = len(respuesta.text) if hasattr(respuesta, 'text') else 0
                            raise Exception(f"Error parseando JSON. La respuesta se cortó en {long_texto} caracteres. Razón de parada de la IA: {finish_reason}. Detalle: {parse_error}")
                        
                        st.session_state.respuestas_usuario = {}
                        
                        if not es_repaso:
                            if doc_main == "📚 Todas las fuentes (Estudio Global)":
                                if doc_a_avanzar:
                                    k = f"{concurso_main}/{doc_a_avanzar}"
                                    prog_doc = st.session_state.progreso_por_doc.get(k, 0.0)
                                    st.session_state.progreso_por_doc[k] = min(prog_doc + avance_pct_doc, 100.0)
                            else:
                                nuevo_progreso = min(progreso_actual + porcentaje_estudio, 100.0)
                                st.session_state.progreso_por_doc[id_progreso] = nuevo_progreso
                            # Guardar en la nube automáticamente
                            sesiones = obtener_sesiones(concurso_main)
                            if st.session_state.sesion_activa in sesiones:
                                sesiones[st.session_state.sesion_activa]["progreso_por_doc"] = st.session_state.progreso_por_doc
                                guardar_sesiones(concurso_main, sesiones)
                        
                        status.update(label="¡Test generado con éxito!", state="complete", expanded=False)
                        st.rerun()
                    except Exception as e:
                        status.update(label="Error en la Inteligencia Artificial", state="error", expanded=True)
                        st.error(f"Error técnico de Gemini: {e}")

    if st.session_state.test_actual:
        st.subheader("📝 Cuestionario Actual")
        
        preguntas = st.session_state.test_actual.get("preguntas", [])
        
        for p in preguntas:
            p_id = p.get('id', '?')
            enunciado = p.get('enunciado', p.get('pregunta', 'Error: La IA no estructuró bien la pregunta.'))
            opciones = p.get('opciones', [])
            correcta_idx = p.get('correcta', 0)
            justificacion = p.get('justificacion', 'Sin justificación.')
            mapa = p.get('mapa_mental', '')
            refran = p.get('refran', '')
            
            st.markdown(f"**Pregunta {p_id}:** {enunciado}")
            respondido = str(p_id) in st.session_state.respuestas_usuario
            
            for i, opcion in enumerate(opciones):
                key = f"btn_{p_id}_{i}"
                
                if not respondido:
                    if st.button(opcion, key=key):
                        st.session_state.respuestas_usuario[str(p_id)] = i
                        st.session_state.estadisticas['total'] += 1
                        if i == correcta_idx:
                            st.session_state.estadisticas['correctas'] += 1
                        else:
                            st.session_state.estadisticas['incorrectas'] += 1
                        st.rerun()
                else:
                    respuesta_dada = st.session_state.respuestas_usuario[str(p_id)]
                    if i == correcta_idx:
                        st.success(f"**✔️ {opcion}** (Respuesta Correcta)")
                    elif i == respuesta_dada and respuesta_dada != correcta_idx:
                        st.error(f"**❌ {opcion}** (Tu Respuesta)")
                    else:
                        st.markdown(f"⚪ {opcion}")
            
            if respondido:
                with st.expander("💡 Ver Explicación Completa", expanded=True):
                    st.info(f"**Justificación:**\n\n{justificacion}")
                    if mapa:
                        st.warning("**Esquema Mental Visual:**\n\n" + mapa.replace('\\n', '\n'))
                    if refran:
                        st.success(f"🎵 **Regla Mnemotécnica (Refrán):**\n\n_{refran}_")
            st.write("---")
            
        if st.button("Repetir el mismo test actual", help="Borra las respuestas y permite volver a intentar las mismas preguntas exactas."):
            st.session_state.respuestas_usuario = {}
            st.rerun()
else:
    st.info("👈 Por favor, abre el botón de un concurso en la barra lateral y presiona '▶️ Seleccionar para estudiar'.")
