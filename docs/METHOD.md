# Metodo implementato: Fuzzy-RESF v0.4

Questa implementazione trasferisce il prototipo numerico usato nei casi studio nel motore del plugin. È un metodo **pilota, geometrico e fuzzy**, con valori esperti fissi; non è un modello addestrato e non è dimostrata una superiorità generale sui metodi della letteratura.

## Modalità Interno

| Passaggio | Algoritmo e parametri | Risultato / limite |
|---|---|---|
| Coordinate | Ripristino `toGlobal3d` di CloudCompare, conversione esplicita in metri, origine numerica locale conservata | Coordinate globali ripristinate nel posizionamento IFC; nessun CRS inventato |
| Riduzione | Voxel di 0,05 m, primo punto originale per cella | Nessuna media dei punti; elaborazione a blocchi |
| Superfici locali | PCA dei 20 vicini, normale = autovettore del minimo autovalore | Planarità `(λ₂−λ₁)/max(λ₃,10⁻¹⁵)` con autovalori crescenti |
| Livello | Istogramma Z da 0,02 m; valutazione delle coppie tra i primi sei picchi, altezza e sovrapposizione XY | Un livello principale, altezza ammessa 1,8–8 m; soffitto e pavimento devono essere osservati |
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

## Selezione del livello nella v0.4

Le quote sono adattate con la mediana dei punti orizzontali entro ±0,08 m dal picco. Ogni superficie occupa un insieme di celle XY da 0,20 m. Una coppia richiede altezza 1,8–8 m, almeno 5 m² di celle per superficie, intersezione pari almeno al 60% dell'insieme minore e al 35% del maggiore. Superfici intermedie con area ≥35% della minore e sovrapposizione ≥35% dell'intersezione invalidano la coppia. Lo score è il numero di celle condivise moltiplicato per la media geometrica dei due rapporti di sovrapposizione. Due intervalli di piano distinti con score entro il 15% provocano una richiesta di ritaglio. È un filtro geometrico, non una prova semantica di pavimento/soffitto: tavoli, terrazzi e rilievi incompleti possono ancora richiedere intervento manuale.

## Modalità Esterno nella v0.4

La nuova modalità non usa la coppia pavimento/soffitto e non impone assi XY alle facciate. Rimane necessaria Z verticale. Usa gli stessi voxel e normali PCA, poi:

1. **Proposte di piano** con RANSAC guidato dalle normali: seme 48021, massimo 16.000 punti e 96 normali candidate per iterazione; soglia iniziale 5 cm, due rifit PCA con banda 6 cm e accordo normale entro 12°. Budget: 28 piani verticali e 18 piani di copertura. Le componenti connesse possono produrre più elementi per piano.
2. **Pareti candidate** da normali con |nz|<0,20. **Coperture candidate** da |nz|≥0,25, osservazioni entro 0,30 m dal massimo della colonna XY da 0,30 m e quota mediana del piano almeno pari al quantile Z 60% della nuvola. Sono criteri geometrici: terrazzi, terreno elevato e superfici di altri oggetti possono essere classificati erroneamente. Ritagliare un edificio.
3. **Contorni nel piano locale**: raster da 0,15 m, closing 3×3, componenti da almeno 1,5 m² per pareti o 4 m² per falde, semplificazione 0,07 m. Espansione di 0,002 m per separare i contatti puntuali tra anelli, che genererebbero spigoli non-manifold nell'estrusione. I vuoti grandi sono conservati senza classificarli come finestre.
4. **Supporto fuzzy monotono** con cinque membership e score equivalente a una base Sugeno completa: `S = 0,25 P + 0,30 R + 0,20 N + 0,10 A + 0,15 C`. Con `r(x;a,b)=clip((x-a)/(b-a),0,1)`: P=r(planarità;0,25;0,65), R=1-r(residuoP95;0,025;0,07), N=r(mediana accordo normali;0,90;0,99), A=r(area;1,5;12), C=r(copertura raster;0,40;0,90). Accettazione con score ≥0,65 e P95≤0,06 m. `geometry_only` rimuove solo la soglia fuzzy. Valori esperti, non calibrati sui sei benchmark.
5. **IFC parametrico**: ogni componente diventa un profilo estruso nel piano misurato, con asse di estrusione normale alla superficie. `IfcWall` per pareti e `IfcRoof` per falde; spessori rispettivamente 0,20 e 0,15 m, **assunti e centrati sulla superficie osservata**. Contenimento nell'edificio, senza piani o stanze inventati. Le falde inclinate mantengono l'orientamento 3D.

Ogni elemento ha evidenze, residui, score e richiesta di revisione. Il modello può contenere porzioni sovrapposte, non raccordate o mancanti: **non è un involucro chiuso certificato**, né un modello già pronto per computi o simulazioni energetiche. Lo schema IFC e la chiusura dei singoli solidi non validano la classificazione o la completezza dell'edificio.

## Cosa rimane da sviluppare

- Generalizzazione della modalità interna a orientamenti multipli e primitive curve.
- Distinzione affidabile tra muri, arredi, facciate e superfici temporanee.
- Visibilità/ray tracing con posizioni di scansione per modellare le occlusioni.
- Aperture con sottrazioni `IfcOpeningElement`, porte e finestre.
- Separazione automatica di più livelli, scale, coperture curve o complesse e impianti.
- Topologia delle stanze validata e benchmark indipendente su tutti i casi studio.
- Calibrazione dei pesi, stima dell'incertezza e confronto statistico con baseline a parità di input.

La logica fuzzy non ricostruisce l'informazione assente: rende esplicito un criterio di supporto e accettazione. Le estensioni devono essere implementate e misurate prima di descriverle come funzionalità operative o più performanti.

## Riferimenti tecnici

- [CloudCompare: istruzioni di compilazione al commit usato](https://github.com/CloudCompare/CloudCompare/blob/001bfd04a13101bfee84a28a6eaa3c7a6338b526/BUILD.md).
- [CloudCompare: conversione delle coordinate globali](https://github.com/CloudCompare/CloudCompare/blob/001bfd04a13101bfee84a28a6eaa3c7a6338b526/libs/qCC_db/include/ccShiftedObject.h).
- [IfcOpenShell](https://github.com/IfcOpenShell/IfcOpenShell): costruzione, validazione e tessellazione IFC.
- [scikit-image](https://github.com/scikit-image/scikit-image): watershed con marcatori.

I riferimenti descrivono le infrastrutture impiegate; non costituiscono una validazione scientifica del metodo proposto.
