import streamlit as st
import io
import json
import PyPDF2
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
    justificacion: str = Field(description="Justificación detallada y pedagógica de la respuesta")
    mapa_mental: str = Field(description="Esquema conceptual horizontal usando flechas (->)")

class TestResult(BaseModel):
    preguntas: list[Pregunta] = Field(description="Lista con exactamente 5 preguntas generadas")

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Estudio Concursos", page_icon="📚", layout="wide")

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
    model = genai.GenerativeModel("gemini-flash-latest")
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
        supabase.storage.from_(BUCKET_NAME).update(file=datos, path='concursos.json', file_options={"content-type": "application/json"})
        descargar_documento_cache.clear()
        return True
    except Exception:
        try:
            supabase.storage.from_(BUCKET_NAME).upload(file=datos, path='concursos.json', file_options={"content-type": "application/json"})
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
        supabase.storage.from_(BUCKET_NAME).update(file=datos, path=ruta, file_options={"content-type": "application/json"})
    except Exception:
        try:
            supabase.storage.from_(BUCKET_NAME).upload(file=datos, path=ruta, file_options={"content-type": "application/json"})
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
        archivos = [f['name'] for f in res if f['name'].endswith(('.pdf', '.txt'))]
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

def extraer_texto_doc(concurso, doc_name):
    """Descarga el documento a la memoria y extrae su texto con barra de progreso."""
    ruta = f"{safe_name(concurso)}/{doc_name}"
    try:
        res = descargar_documento_cache(ruta)
        if doc_name.endswith('.pdf'):
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(res))
            texto = ""
            total_pages = len(pdf_reader.pages)
            progreso_lectura = st.progress(0.0, text=f"Preparando lectura de {total_pages} páginas...")
            for i, page in enumerate(pdf_reader.pages):
                extracted = page.extract_text()
                if extracted:
                    texto += extracted + "\n"
                porcentaje = (i + 1) / total_pages
                progreso_lectura.progress(porcentaje, text=f"Extrayendo texto del PDF... Página {i+1} de {total_pages}")
            progreso_lectura.empty()
            return texto
        elif doc_name.endswith('.txt'):
            return res.decode('utf-8')
    except Exception as e:
        st.error(f"Error al leer el documento: {e}")
        return None

# --- INTERFAZ BARRA LATERAL ---
with st.sidebar:
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
                        col1.write(f"📄 {doc}")
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
                doc_seleccionado_ui = st.selectbox("Selecciona fuente:", documentos, key=f"doc_{conc}")
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
    
    if id_progreso not in st.session_state.progreso_por_doc:
        st.session_state.progreso_por_doc[id_progreso] = 0.0
    
    progreso_actual = st.session_state.progreso_por_doc[id_progreso]
    
    st.progress(progreso_actual / 100.0, text=f"Progreso de lectura acumulado: {progreso_actual:.1f}%")
    
    porcentaje_estudio = st.slider("¿Qué porcentaje de la fuente deseas abarcar en el próximo test?", min_value=5, max_value=50, value=10, step=5)
    
    col_btn1, col_btn2 = st.columns(2)
    generar_avance = col_btn1.button("🚀 Avanzar materia nueva", type="primary", use_container_width=True, help="Avanza tu progreso y genera preguntas del material no visto.")
    generar_repaso = col_btn2.button("🔄 Repasar lo ya estudiado", use_container_width=True, help="Elige un fragmento al azar de lo que ya has leído para afianzar conocimientos sin avanzar.")

    if generar_avance or generar_repaso:
        es_repaso = generar_repaso
        if es_repaso and progreso_actual == 0.0:
            st.warning("Aún no tienes progreso en este documento para repasar. Haz clic en 'Avanzar materia nueva' primero.")
        else:
            with st.status("Preparando tu test...", expanded=True) as status:
                st.write("📖 Extrayendo texto del documento...")
                texto_completo = extraer_texto_doc(concurso_main, doc_main)
                if texto_completo:
                    total_chars = len(texto_completo)
                    
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
                    
                    texto_seccion = texto_completo[inicio_idx:fin_idx]
                    
                    if not texto_seccion.strip():
                        status.update(label="Error de progreso", state="error", expanded=True)
                        st.warning("Ya has completado este documento o la sección no contiene texto. Reinicia tu progreso para empezar de nuevo.")
                    else:
                        st.write("🤖 Analizando el texto y estructurando preguntas con IA...")
                        prompt = f"""
                        Actúa como un experto en la creación de exámenes para concursos de méritos públicos.
                        A continuación te proporciono un extracto del temario:
                        
                        --- INICIO DEL EXTRACTO ---
                        {texto_seccion}
                        --- FIN DEL EXTRACTO ---
                        
                        INSTRUCCIONES ESTRICTAS DE FORMATO:
                        1. Genera EXACTAMENTE 5 preguntas basadas en este texto.
                        2. REGLA OBLIGATORIA: Cada pregunta DEBE tener las claves 'enunciado', 'justificacion' y 'mapa_mental'.
                        3. La "justificacion" debe ser detallada y pedagógica. Explica ampliamente por qué la opción es correcta.
                        4. El "mapa_mental" debe ser un esquema conceptual horizontal corto y directo usando flechas. (Ejemplo: Concepto -> Propiedad -> Detalle).
                        5. REGLA DE SEGURIDAD CRÍTICA: Todo el contenido debe ser 100% PARAFRASEADO usando tus propias palabras. NO COPIES NINGÚN TEXTO LITERAL del documento.
                        6. Devuelve ÚNICAMENTE la estructura JSON estricta.
                        """
                        
                        try:
                            respuesta = model.generate_content(
                                prompt,
                                generation_config=genai.GenerationConfig(
                                    response_mime_type="application/json",
                                    response_schema=TestResult,
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
            st.write("---")
            
        if st.button("Repetir el mismo test actual", help="Borra las respuestas y permite volver a intentar las mismas preguntas exactas."):
            st.session_state.respuestas_usuario = {}
            st.rerun()
else:
    st.info("👈 Por favor, abre el botón de un concurso en la barra lateral y presiona '▶️ Seleccionar para estudiar'.")
