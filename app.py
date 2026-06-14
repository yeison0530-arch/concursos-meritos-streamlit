import streamlit as st
import io
import json
import PyPDF2
from supabase import create_client, Client
import google.generativeai as genai
import typing_extensions as typing

# --- TIPOS DE DATOS PARA GEMINI ---
class Pregunta(typing.TypedDict):
    id: int
    enunciado: str
    opciones: list[str]
    correcta: int
    justificacion: str

class TestResult(typing.TypedDict):
    preguntas: list[Pregunta]

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Estudio Concursos", page_icon="📚", layout="wide")

# --- INICIALIZACIÓN DE SESSION STATE ---
if 'progreso_por_doc' not in st.session_state:
    st.session_state.progreso_por_doc = {} # Historial de progreso por cada documento
if 'estadisticas' not in st.session_state:
    st.session_state.estadisticas = {'total': 0, 'correctas': 0, 'incorrectas': 0}
if 'test_actual' not in st.session_state:
    st.session_state.test_actual = None
if 'respuestas_usuario' not in st.session_state:
    st.session_state.respuestas_usuario = {}

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
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    st.error(f"Error al configurar la API de Gemini: {e}")
    st.stop()

# --- FUNCIONES DE ALMACENAMIENTO ---
def listar_documentos():
    """Obtiene la lista de documentos en el bucket."""
    try:
        res = supabase.storage.from_(BUCKET_NAME).list()
        # Filtramos por archivos válidos
        archivos = [f['name'] for f in res if f['name'].endswith(('.pdf', '.txt'))]
        return archivos
    except Exception as e:
        st.sidebar.error(f"Error al listar documentos: {e}")
        return []

def subir_documento(file):
    """Sube un archivo directamente a Supabase Storage."""
    try:
        file_bytes = file.getvalue()
        # file_options ajusta el mime type adecuado
        res = supabase.storage.from_(BUCKET_NAME).upload(
            file=file_bytes,
            path=file.name,
            file_options={"content-type": file.type}
        )
        st.sidebar.success(f"Archivo {file.name} subido exitosamente.")
    except Exception as e:
        st.sidebar.error(f"Error al subir el archivo (puede que ya exista con ese nombre): {e}")

def extraer_texto_doc(doc_name):
    """Descarga el documento a la memoria y extrae su texto."""
    try:
        res = supabase.storage.from_(BUCKET_NAME).download(doc_name)
        if doc_name.endswith('.pdf'):
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(res))
            texto = ""
            for page in pdf_reader.pages:
                extracted = page.extract_text()
                if extracted:
                    texto += extracted + "\n"
            return texto
        elif doc_name.endswith('.txt'):
            return res.decode('utf-8')
    except Exception as e:
        st.error(f"Error al leer el documento {doc_name}: {e}")
        return None

# --- INTERFAZ BARRA LATERAL ---
with st.sidebar:
    st.header("📂 Gestión de Documentos")
    uploaded_file = st.file_uploader("Sube un nuevo temario (PDF o TXT)", type=["pdf", "txt"])
    if uploaded_file is not None:
        if st.button("Guardar en la nube"):
            subir_documento(uploaded_file)
            st.rerun()
            
    st.divider()
    st.header("📑 Mis Temarios")
    documentos = listar_documentos()
    
    doc_seleccionado = None
    if documentos:
        doc_seleccionado = st.selectbox("Selecciona un documento para estudiar", documentos)
    else:
        st.info("No hay documentos disponibles. Sube uno primero.")

# --- LÓGICA PRINCIPAL ---
st.title("📚 Creador de Tests para Concursos")

# Panel de Estadísticas en la parte superior
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

if doc_seleccionado:
    st.subheader(f"Estudiando: {doc_seleccionado}")
    
    # Manejar el progreso acumulado en la sesión
    if doc_seleccionado not in st.session_state.progreso_por_doc:
        st.session_state.progreso_por_doc[doc_seleccionado] = 0.0
    
    progreso_actual = st.session_state.progreso_por_doc[doc_seleccionado]
    
    st.progress(progreso_actual / 100.0, text=f"Progreso de lectura acumulado: {progreso_actual:.1f}%")
    
    porcentaje_estudio = st.slider("¿Qué porcentaje de la fuente deseas abarcar en el próximo test?", min_value=5, max_value=50, value=10, step=5)
    
    # Botón principal para generar el test
    if st.button("Generar nuevo test", type="primary"):
        with st.spinner("Leyendo la fuente y generando preguntas con Inteligencia Artificial..."):
            texto_completo = extraer_texto_doc(doc_seleccionado)
            if texto_completo:
                # Calcular qué porción de texto vamos a enviar a la IA
                total_chars = len(texto_completo)
                inicio_idx = int((progreso_actual / 100.0) * total_chars)
                fin_idx = int(((progreso_actual + porcentaje_estudio) / 100.0) * total_chars)
                fin_idx = min(fin_idx, total_chars) # Evitar desbordamiento
                
                texto_seccion = texto_completo[inicio_idx:fin_idx]
                
                if not texto_seccion.strip():
                    st.warning("Ya has completado este documento o la sección no contiene texto. Por favor reinicia tu progreso para volver a empezar.")
                else:
                    # Instrucciones para la API
                    prompt = f"""
                    Actúa como un Desarrollador Senior y experto en la creación de exámenes para concursos de méritos públicos.
                    A continuación te proporciono un extracto del temario que el usuario debe estudiar:
                    
                    --- INICIO DEL EXTRACTO ---
                    {texto_seccion}
                    --- FIN DEL EXTRACTO ---
                    
                    Genera un test con preguntas de opción múltiple (mínimo 3, máximo 5) basadas EXCLUSIVAMENTE en este texto.
                    Asegúrate de que haya una única opción correcta por pregunta.
                    La justificación debe explicar de forma clara por qué la opción correcta lo es, basándose en la información del texto.
                    """
                    
                    try:
                        # Petición a Gemini usando Structured Outputs para asegurar el esquema JSON
                        respuesta = model.generate_content(
                            prompt,
                            generation_config=genai.GenerationConfig(
                                response_mime_type="application/json",
                                response_schema=TestResult,
                                temperature=0.2
                            )
                        )
                        # Parseamos la respuesta a un diccionario Python
                        st.session_state.test_actual = json.loads(respuesta.text)
                        # Limpiamos las respuestas del test anterior
                        st.session_state.respuestas_usuario = {}
                        
                        # Actualizar progreso
                        nuevo_progreso = min(progreso_actual + porcentaje_estudio, 100.0)
                        st.session_state.progreso_por_doc[doc_seleccionado] = nuevo_progreso
                        st.rerun() # Recargamos para mostrar el nuevo test
                    except Exception as e:
                        st.error(f"Error al generar el test con la API de Gemini: {e}")

    # Mostrar Test Actual en curso
    if st.session_state.test_actual:
        st.subheader("📝 Cuestionario Actual")
        
        preguntas = st.session_state.test_actual.get("preguntas", [])
        
        for p in preguntas:
            st.markdown(f"**Pregunta {p['id']}:** {p['enunciado']}")
            
            opciones = p['opciones']
            correcta_idx = p['correcta']
            
            # Variables de estado de respuesta para esta pregunta
            respondido = str(p['id']) in st.session_state.respuestas_usuario
            
            # Mostrar botones de opciones
            for i, opcion in enumerate(opciones):
                key = f"btn_{p['id']}_{i}"
                
                if not respondido:
                    # El usuario aún no ha respondido, mostramos botones clickeables
                    if st.button(opcion, key=key):
                        # Registrar respuesta
                        st.session_state.respuestas_usuario[str(p['id'])] = i
                        # Actualizar estadísticas globales
                        st.session_state.estadisticas['total'] += 1
                        if i == correcta_idx:
                            st.session_state.estadisticas['correctas'] += 1
                        else:
                            st.session_state.estadisticas['incorrectas'] += 1
                        st.rerun() # Refrescar la página Inmediatamente
                else:
                    # El usuario ya respondió, mostramos feedback visual
                    respuesta_dada = st.session_state.respuestas_usuario[str(p['id'])]
                    
                    if i == correcta_idx:
                        st.success(f"**✔️ {opcion}** (Respuesta Correcta)")
                    elif i == respuesta_dada and respuesta_dada != correcta_idx:
                        st.error(f"**❌ {opcion}** (Tu Respuesta)")
                    else:
                        st.markdown(f"⚪ {opcion}")
            
            # Mostrar justificación si la pregunta ya fue respondida
            if respondido:
                with st.expander("Ver Justificación", expanded=True):
                    st.info(p['justificacion'])
            st.write("---")
            
        # Botón para repetir las mismas preguntas
        if st.button("Repetir test actual"):
            st.session_state.respuestas_usuario = {}
            st.rerun()
else:
    st.info("👈 Selecciona un documento existente o sube uno nuevo en la barra lateral para comenzar.")
