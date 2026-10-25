# Installazione e compilazione

## Compatibilità

Il plugin usa l'API Standard di **CloudCompare 2.14 / Qt 6**. La CI fissa il sorgente CloudCompare al commit `001bfd04a13101bfee84a28a6eaa3c7a6338b526`. Compilare applicazione e plugin insieme evita incompatibilità ABI. Non copiare la DLL in CloudCompare 2.13 / Qt 5 o in una build con compilatore/Qt differenti.

Il motore è incorporato come sorgente Python nelle risorse del plugin. Le librerie scientifiche si installano una volta in un ambiente Python esterno; non serve il plugin PythonRuntime di CloudCompare. Target Python verificato: **3.12 x64**.

## Pacchetti CI

Nella scheda [Actions](https://github.com/ingmassimilianopepe-rgb/qFuzzyRESF/actions), aprire un workflow **riuscito** e scaricare l'artifact `qFuzzyRESF-Windows-CloudCompare-001bfd0` o Linux. Gli artifact hanno conservazione di 30 giorni. Su Windows il pacchetto contiene applicazione, plugin e runtime Qt della stessa build. Python e le sue dipendenze restano da installare.

Questi pacchetti sono build di prova, non installer firmati o release stabili. L'avvio e il flusso grafico completo vanno ancora verificati su una postazione con interfaccia grafica. Su Linux il pacchetto richiede le librerie Qt/OpenGL e LASzip del sistema compatibile con il runner Ubuntu 24.04. Se estratto in una cartella diversa dal prefisso di compilazione, avviarlo dalla cartella estratta con:

```bash
LD_LIBRARY_PATH="$PWD/lib/cloudcompare${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ./bin/CloudCompare
```

Per installare il motore Python su Linux usare gli script del repository, non inclusi nell'artifact nativo Linux.

## Ambiente Python

Dalla cartella del progetto o del pacchetto Windows:

```powershell
# Windows: Python 3.12 x64 deve essere già installato
.\scripts\install_backend.ps1
```

```bash
# Linux: Python 3.12 con modulo venv
sh scripts/install_backend.sh
```

Se una policy aziendale impedisce gli script, creare l'ambiente manualmente senza modificarla:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe backend\run_pipeline.py --check
```

Nel dialogo selezionare `.venv/Scripts/python.exe` (Windows) o `.venv/bin/python` (Linux), senza argomenti aggiunti. Percorsi con spazi sono supportati: il plugin passa gli argomenti tramite `QProcess`, senza shell.

## Compilazione da sorgente

Installare CMake ≥3.20, compilatore C++17 e Qt 6. Su Windows usare Visual Studio 2022 x64 e Qt 6.8.3 `msvc2022_64`. Su Ubuntu 24.04:

```bash
sudo apt install build-essential cmake qt6-base-dev qt6-svg-dev qt6-tools-dev qt6-tools-dev-tools libqt6opengl6-dev libgl1-mesa-dev liblaszip-dev
```

Preparare il sorgente e i sottomoduli necessari:

```bash
git clone https://github.com/CloudCompare/CloudCompare.git
cd CloudCompare
git checkout 001bfd04a13101bfee84a28a6eaa3c7a6338b526
git submodule update --init --recursive --depth 1 libs/qCC_db/extern/CCCoreLib libs/qPCP libs/CCAppCommon/QDarkStyleSheet plugins/core/IO/MeshIO
git clone --branch feature/cloudcompare-ifc-tool https://github.com/ingmassimilianopepe-rgb/qFuzzyRESF.git plugins/private/qFuzzyRESF
```

Da questa cartella:

```bash
cmake -S . -B build -DPLUGIN_STANDARD_QFUZZYRESF=ON -DOPTION_BUILD_CCVIEWER=OFF -DCCCORELIB_USE_TBB=OFF -DCCCORELIB_USE_CGAL=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=../dist
cmake --build build --config Release --parallel 3
cmake --install build --config Release
```

Su Windows aggiungere alla configurazione, ad esempio, `-DCMAKE_PREFIX_PATH=C:/Qt/6.8.3/msvc2022_64`, poi eseguire `windeployqt --release` sull'eseguibile installato. Il nome della cartella del plugin deve essere **qFuzzyRESF**: il sistema di build di CloudCompare cerca la risorsa `qFuzzyRESF.qrc` in base a quel nome.

I pacchetti CI includono il lettore LAS/LAZ (`PLUGIN_IO_QLAS=ON`). Su Linux la dipendenza è `liblaszip-dev`; su Windows la CI compila LASzip 3.5.0 dal commit `fa089bd8b2b4ca5631e199d257374c32a125f73f`, lo installa in `laszip-install` e aggiunge quella cartella a `CMAKE_PREFIX_PATH`. Per includerlo nella compilazione manuale usare lo stesso procedimento e aggiungere `-DPLUGIN_IO_QLAS=ON`.

La build minima non include tutti gli altri lettori opzionali di CloudCompare (ad esempio E57). Per questi formati compilare e abilitare il relativo plugin I/O seguendo le istruzioni ufficiali oppure convertire la nuvola in un formato già leggibile. Il motore standalone legge LAS/LAZ.

## Problemi frequenti

| Messaggio / sintomo | Azione |
|---|---|
| Plugin assente | Controllare compatibilità della build, opzione CMake e log plugin di CloudCompare |
| Azione disabilitata | Selezionare una sola vera nuvola nell'albero DB, non un gruppo o una mesh |
| Avvio Python fallito | Selezionare il percorso completo dell'interprete del venv |
| `ModuleNotFoundError` | Installare `backend/requirements.txt` con quello stesso interprete |
| Due piani orizzontali mancanti | Verificare Z verticale, unità e presenza di pavimento/soffitto; ritagliare il livello |
| Limite voxel/raster | Ritagliare la nuvola; aumentare il limite voxel solo con RAM sufficiente |
| Output esistente | Scegliere un nuovo nome; anche la cartella `.fuzzyresf` deve essere libera |
| Errore di pubblicazione su volume remoto | Usare una cartella locale che supporti hard link (ad esempio NTFS/ext4); il salvataggio atomico non ripiega su copie incomplete |
| Tante pareti omesse o confini virtuali | Verificare allineamento, densità, occlusioni e compatibilità con l'ipotesi Manhattan |

Per una diagnosi conservare i quattro JSON accanto all'IFC e la versione del plugin. Il codice non carica la nuvola o il modello su servizi remoti.
