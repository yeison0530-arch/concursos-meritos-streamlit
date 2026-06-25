import json

progress = {
    "ley_1437": 2.406288433773595,
    "Ley_1952": 8.25587084148728,
    "Ley_80": 10.691516743443197
}

last_global_doc = None
docs_del_concurso = ["ley_1437", "Ley_1952", "Ley_80"]

# Advance amounts (approximate based on their lengths)
# 1437: 2.40%
# 1952: 8.25%
# 80: 10.69%

for i in range(15):
    docs_pendientes = [(d, progress[d]) for d in docs_del_concurso if progress[d] < 100.0]
    docs_pendientes.sort(key=lambda x: x[1])
    candidatos = [d for d in docs_pendientes if d[0] != last_global_doc]
    
    doc_elegido = candidatos[0][0]
    prog_doc = candidatos[0][1]
    last_global_doc = doc_elegido
    
    # Calculate advance
    if doc_elegido == "ley_1437": adv = 2.406
    elif doc_elegido == "Ley_1952": adv = 8.255
    else: adv = 10.691
    
    progress[doc_elegido] = min(progress[doc_elegido] + adv, 100.0)
    
    print(f"Click {i+1}: Picked {doc_elegido} (Old Prog: {prog_doc:.2f}% -> New Prog: {progress[doc_elegido]:.2f}%)")
