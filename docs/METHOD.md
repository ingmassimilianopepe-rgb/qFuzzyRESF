# Metodo implementato: Fuzzy-RESF v0.3

Questa implementazione trasferisce il prototipo numerico usato nei casi studio nel motore del plugin. È un metodo **pilota, geometrico e fuzzy**, con valori esperti fissi; non è un modello addestrato e non è dimostrata una superiorità generale sui metodi della letteratura.

## Sequenza operativa

| Passaggio | Algoritmo e parametri | Risultato / limite |
|---|---|---|
| Coordinate | Ripristino `toGlobal3d` di CloudCompare, conversione esplicita in metri, origine numerica locale conservata | Coordinate globali ripristinate nel posizionamento IFC; nessun CRS inventato |
| Riduzione | Voxel di 0,05 m, primo punto originale per cella | Nessuna media dei punti; elaborazione a blocchi |
| Superfici locali | PCA dei 20 vicini, normale = autovettore del minimo autovalore | Planarità `(λ₂−λ₁)/max(λ₃,10⁻¹⁵)` con autovalori crescenti |
| Livello | Istogramma Z da 0,02 m delle superfici orizzontali; due picchi più forti | Un livello principale, altezza ammessa 1,8–8 m; soffitto e pavimento devono essere osservati |
| Orientamento | Mediana delle normali verticali ripiegate modulo 90°, limitata a ±10° intorno agli assi iniziali | Non è un estimatore generale dell'orientamento: preparare la nuvola in CloudCompare |
| Proposte di parete | Due famiglie ortogonali; istogrammi da 0,025 m, filtro gaussiano σ=0,8, picchi multipli | Tolleranza angolare 12°, banda finale ±0,045 m, almeno 120 punti e 0,8 m di sviluppo |
| Supporto fuzzy | Cinque membership e Sugeno monotono, soglia 0,70 | Indice di supporto esperto; non probabilità |
| Controllo geometrico | Residuo P95 ≤0,05 m, estensione verticale ≥50% dell'altezza | Condiviso da tutti i profili sperimentali |
| Spessori | Accoppiamento di facce parallele a 0,075–0,45 m, sovrapposizione >65% dello sviluppo massimo | Faccia singola: 0,12 m assunto e marcato come tale |
| Topologia | Deduplicazione, chiusura di lacune ≤1,2 m, aggancio estremi entro 0,45 m | Può coprire porte reali; non è riconoscimento delle occlusioni |
| Involucro in pianta | Supporto del soffitto, raster 0,10 m, closing 5×5, riempimento delle lacune | Impronta inferita; corti e vuoti possono essere riempiti erroneamente |
| Spazi | Involucro meno pareti con fascia 0,025 m; distance transform e watershed con marcatori | Area ≥2 m²; nucleo dopo erosione 0,35 m; spazi provvisori |
| BIM | Profili estrusi IFC4 con eventuali vuoti interni, gerarchia spaziale, quantità, evidenze | Modello parametrico editabile; non una mesh travestita da muro |
| Controlli | Schema EXPRESS, tessellazione dei solidi, chiusura delle mesh, sovrapposizioni tra spazi | Coerenza formale e geometrica; accuratezza rispetto al rilievo da valutare separatamente |

La concentrazione angolare a quattro lobi viene registrata come diagnostica; sotto 0,5 si segnala che l'ipotesi Manhattan è debole. Non viene usata per dichiarare corretta una ricostruzione o per eliminare automaticamente geometrie.

## Membership e score

Definire `r(x;a,b)=clip((x−a)/(b−a),0,1)`.

| Membership | Misura della proposta | Trasformazione |
|---|---|---|
| V | Mediana `1−abs(nz)` | `r(V;0,75;0,95)` |
| P | Mediana della planarità PCA | `r(P;0,25;0,65)` |
| C | Frazione di 10 fasce verticali con ≥5 punti | `r(C;0,30;0,75)` |
| F | Distanza dal pavimento del quantile Z al 2% | `1−r(gap;0,15;0,70)` |
| L | Frazione di intervalli longitudinali da 0,10 m con ≥3 punti | `r(L;0,50;0,90)` |

Lo score è `S = 0,20 μV + 0,20 μP + 0,25 μC + 0,15 μF + 0,20 μL`.

È esattamente la semplificazione di una base completa Sugeno di ordine zero con 32 antecedenti binari, partizioni complementari a prodotto e conseguenti singleton uguali alla somma pesata degli antecedenti. È monotono rispetto alle cinque membership. Il codice usa direttamente la somma equivalente, senza simulare inutilmente 32 regole. Pesi e soglie non sono appresi o calibrati.

Il profilo `legacy` conserva la precedente combinazione normalizzata di sette attivazioni minime, riportata nel codice e nei report. `geometry_only` usa esclusivamente i vincoli geometrici. Le membership e lo score monotono vengono calcolati in tutti i profili; lo `score` delle pareti riportato nell'IFC resta l'indice monotono, mentre il profilo che determina l'accettazione è registrato nel progetto.

## Spazi interni

Il watershed usa il negativo della distanza dalle pareti sul dominio libero. I marcatori hanno distanza minima dalle pareti di 1 m, finestra di massimo locale 41 celle e separazione minima di 3 m. Ogni componente libera di almeno 2 m² riceve almeno un marcatore. Non viene imposto un numero di stanze e non viene letto un modello di riferimento.

Le relazioni `IfcRelSpaceBoundary` sono di primo livello. Un bordo è associato a una parete candidata se tre punti del segmento distano al massimo 0,13 m dal relativo poligono. Gli altri bordi hanno `IfcVirtualElement`, senza geometria di parete inventata. I confini virtuali possono essere numerosi a causa della rasterizzazione e dell'assenza di chiusure osservate. Non sono confini termici certificati né relazioni di secondo livello.

## Cosa rimane da sviluppare

- Estrazione multi-orientamento e primitive curve.
- Distinzione affidabile tra muri, arredi, facciate e superfici temporanee.
- Visibilità/ray tracing con posizioni di scansione per modellare le occlusioni.
- Aperture con sottrazioni `IfcOpeningElement`, porte e finestre.
- Separazione automatica di più livelli, scale, coperture e impianti.
- Topologia delle stanze validata e benchmark indipendente su tutti i casi studio.
- Calibrazione dei pesi, stima dell'incertezza e confronto statistico con baseline a parità di input.

La logica fuzzy non ricostruisce l'informazione assente: rende esplicito un criterio di supporto e accettazione. Le estensioni devono essere implementate e misurate prima di descriverle come funzionalità operative o più performanti.

## Riferimenti tecnici

- [CloudCompare: istruzioni di compilazione al commit usato](https://github.com/CloudCompare/CloudCompare/blob/001bfd04a13101bfee84a28a6eaa3c7a6338b526/BUILD.md).
- [CloudCompare: conversione delle coordinate globali](https://github.com/CloudCompare/CloudCompare/blob/001bfd04a13101bfee84a28a6eaa3c7a6338b526/libs/qCC_db/include/ccShiftedObject.h).
- [IfcOpenShell](https://github.com/IfcOpenShell/IfcOpenShell): costruzione, validazione e tessellazione IFC.
- [scikit-image](https://github.com/scikit-image/scikit-image): watershed con marcatori.

I riferimenti descrivono le infrastrutture impiegate; non costituiscono una validazione scientifica del metodo proposto.
