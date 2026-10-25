// SPDX-License-Identifier: GPL-3.0-or-later
#include "FuzzyDialog.h"
#include <ccPointCloud.h>
#include <QComboBox>
#include <QCoreApplication>
#include <QDataStream>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QProgressBar>
#include <QProcessEnvironment>
#include <QPushButton>
#include <QSettings>
#include <QSpinBox>
#include <QStandardPaths>
#include <QTimer>
#include <QVBoxLayout>
#include <algorithm>
#include <cmath>
#include <stdexcept>

FuzzyDialog::FuzzyDialog(ccPointCloud* cloud, QWidget* parent)
    : QDialog(parent), m_cloud(cloud)
{
    setWindowTitle(tr("qFuzzyRESF · dalla nuvola al BIM IFC"));
    setModal(true);
    resize(750, 600);
    QSettings settings;
    settings.beginGroup("qFuzzyRESF");
    auto* layout = new QVBoxLayout(this);
    auto* title = new QLabel(tr("Nuvola: %1 · %2 punti").arg(cloud->getName()).arg(cloud->size()));
    title->setTextFormat(Qt::PlainText);
    layout->addWidget(title);
    auto* scope = new QLabel(tr("Metodo pilota v0.3: un livello, pareti prevalentemente ortogonali, pavimento, soffitto e spazi interni provvisori. Porte, finestre e pareti curve richiedono modellazione successiva. Z deve essere verticale; allineare le pareti principali a X/Y (±10°)."));
    scope->setWordWrap(true);
    layout->addWidget(scope);
    auto* form = new QFormLayout;
    m_python = new QLineEdit(settings.value("python", "python3").toString());
    m_python->setObjectName("pythonPath");
    auto* pyRow = new QHBoxLayout;
    pyRow->addWidget(m_python);
    auto* pyBrowse = new QPushButton(tr("Sfoglia…"));
    pyRow->addWidget(pyBrowse);
    connect(pyBrowse, &QPushButton::clicked, this, [this] {
        if (m_busy) return;
        const auto path = QFileDialog::getOpenFileName(this, tr("Interprete dell'ambiente Python"), m_python->text());
        if (!path.isEmpty()) m_python->setText(path);
    });
    form->addRow(tr("Eseguibile Python"), pyRow);
    m_output = new QLineEdit;
    m_output->setObjectName("outputPath");
    auto* outRow = new QHBoxLayout;
    outRow->addWidget(m_output);
    auto* outBrowse = new QPushButton(tr("Salva come…"));
    outRow->addWidget(outBrowse);
    connect(outBrowse, &QPushButton::clicked, this, [this] {
        if (m_busy) return;
        QSettings config;
        auto folder = config.value("qFuzzyRESF/outputDir", QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation)).toString();
        auto path = QFileDialog::getSaveFileName(this, tr("Nuovo modello IFC"), folder + "/FuzzyRESF_BIM.ifc", "IFC (*.ifc)", nullptr, QFileDialog::DontConfirmOverwrite);
        if (!path.isEmpty()) {
            if (!path.endsWith(".ifc", Qt::CaseInsensitive)) path += ".ifc";
            m_output->setText(path);
        }
    });
    form->addRow(tr("Nuovo file IFC"), outRow);
    m_units = new QComboBox;
    m_units->setObjectName("sourceUnits");
    m_units->addItem(tr("Metri"), 1.);
    m_units->addItem(tr("Centimetri"), .01);
    m_units->addItem(tr("Millimetri"), .001);
    m_units->setCurrentIndex(settings.value("units", 0).toInt());
    form->addRow(tr("Unità delle coordinate originali"), m_units);
    m_profile = new QComboBox;
    m_profile->addItem(tr("Fuzzy monotono v0.3 (predefinito)"), "monotone");
    m_profile->addItem(tr("Solo geometria (confronto sperimentale)"), "geometry_only");
    m_profile->addItem(tr("Fuzzy legacy (confronto sperimentale)"), "legacy");
    form->addRow(tr("Profilo"), m_profile);
    m_limit = new QSpinBox;
    m_limit->setRange(1, 20);
    m_limit->setValue(3);
    m_limit->setSuffix(tr(" milioni"));
    form->addRow(tr("Limite voxel (aumenta l'uso RAM)"), m_limit);
    layout->addLayout(form);
    auto* note = new QLabel(tr("Il Global Shift/Scale di CloudCompare viene ripristinato. Il modello e i report sono elaborati localmente; scegliere un nome di uscita non ancora esistente."));
    note->setWordWrap(true);
    layout->addWidget(note);
    m_progress = new QProgressBar;
    layout->addWidget(m_progress);
    m_log = new QPlainTextEdit;
    m_log->setReadOnly(true);
    m_log->setMaximumBlockCount(800);
    layout->addWidget(m_log);
    auto* buttons = new QHBoxLayout;
    buttons->addStretch();
    m_start = new QPushButton(tr("Genera IFC"));
    m_start->setObjectName("generateIfc");
    m_cancel = new QPushButton(tr("Chiudi"));
    buttons->addWidget(m_start);
    buttons->addWidget(m_cancel);
    layout->addLayout(buttons);
    connect(m_start, &QPushButton::clicked, this, &FuzzyDialog::start);
    connect(m_cancel, &QPushButton::clicked, this, &FuzzyDialog::reject);
    connect(&m_process, &QProcess::readyReadStandardOutput, this, &FuzzyDialog::readOutput);
    connect(&m_process, &QProcess::readyReadStandardError, this, [this] { log(QString::fromUtf8(m_process.readAllStandardError()).left(8192)); });
    connect(&m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, &FuzzyDialog::finished);
    connect(&m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) {
            log(tr("Avvio Python fallito: %1. Selezionare l'eseguibile dell'ambiente installato con scripts/install_backend.").arg(m_process.errorString()));
            setBusy(false);
            m_work.reset();
        }
    });
}

FuzzyDialog::~FuzzyDialog()
{
    if (m_process.state() != QProcess::NotRunning) {
        m_process.kill();
        m_process.waitForFinished(3000);
    }
}

void FuzzyDialog::log(const QString& message) { m_log->appendPlainText(message); }

void FuzzyDialog::setBusy(bool busy)
{
    m_busy = busy;
    m_start->setEnabled(!busy);
    for (QWidget* widget : QList<QWidget*>{m_python, m_output, m_units, m_profile, m_limit})
        widget->setEnabled(!busy);
    m_cancel->setText(busy ? tr("Annulla") : tr("Chiudi"));
}

void FuzzyDialog::reject()
{
    if (!m_busy) { QDialog::reject(); return; }
    if (m_cancelled) return;
    m_cancelled = true;
    log(tr("Annullamento in corso…"));
    m_process.terminate();
    QTimer::singleShot(3000, this, [this] {
        if (m_cancelled && m_process.state() != QProcess::NotRunning) m_process.kill();
    });
}

bool FuzzyDialog::prepareJob(QString& requestPath)
{
    const auto destination = QFileInfo(m_output->text()).absoluteFilePath();
    const QFileInfo output(destination);
    if (output.suffix().compare("ifc", Qt::CaseInsensitive) != 0 || !output.dir().exists())
        throw std::runtime_error("Scegliere un file .ifc in una cartella esistente.");
    if (output.exists() || QFileInfo(output.dir().filePath(output.completeBaseName() + ".fuzzyresf")).exists())
        throw std::runtime_error("Output già esistente: scegliere un nuovo nome.");
    m_work = std::make_unique<QTemporaryDir>(output.absolutePath() + "/.qfuzzyresf-job-XXXXXX");
    if (!m_work->isValid()) throw std::runtime_error("Impossibile creare la cartella temporanea.");
    const QStringList files = {"run_pipeline.py", "fuzzyresf/__init__.py", "fuzzyresf/cli.py", "fuzzyresf/cloud.py", "fuzzyresf/inference.py", "fuzzyresf/ifc.py"};
    QDir().mkpath(m_work->filePath("fuzzyresf"));
    for (const auto& file : files) {
        QFile resource(":/CC/plugin/qFuzzyRESF/backend/" + file);
        if (!resource.copy(m_work->filePath(file))) throw std::runtime_error("Estrazione del motore Python fallita.");
    }
    QFile binary(m_work->filePath("selected.xyz64"));
    if (!binary.open(QIODevice::WriteOnly)) throw std::runtime_error("Impossibile scrivere la nuvola temporanea.");
    log(tr("Esportazione della nuvola selezionata con coordinate globali…"));
    const unsigned count = m_cloud->size();
    for (unsigned start = 0; start < count; start += 16384) {
        QByteArray block;
        block.reserve(16384 * 24);
        QDataStream data(&block, QIODevice::WriteOnly);
        data.setByteOrder(QDataStream::LittleEndian);
        data.setFloatingPointPrecision(QDataStream::DoublePrecision);
        for (unsigned i = start; i < std::min(count, start + 16384); ++i) {
            const CCVector3d point = m_cloud->toGlobal3d(*m_cloud->getPoint(i));
            if (!std::isfinite(point.x) || !std::isfinite(point.y) || !std::isfinite(point.z))
                throw std::runtime_error("Coordinate NaN/Inf: pulire la nuvola.");
            data << point.x << point.y << point.z;
        }
        if (binary.write(block) != block.size()) throw std::runtime_error("Scrittura della nuvola fallita (spazio disco?).");
        m_progress->setValue(static_cast<int>(100. * std::min(count, start + 16384) / count));
        QCoreApplication::processEvents();
        if (m_cancelled) return false;
    }
    if (!binary.flush()) throw std::runtime_error("Salvataggio della nuvola fallito.");
    binary.close();
    const auto& shift = m_cloud->getGlobalShift();
    QJsonObject job{
        {"schema_version", 1},
        {"input", QJsonObject{{"path", binary.fileName()}, {"format", "xyz64"}, {"count", double(count)}, {"name", m_cloud->getName()}}},
        {"output", destination}, {"work_dir", m_work->path()},
        {"meters_per_unit", m_units->currentData().toDouble()},
        {"profile", m_profile->currentData().toString()},
        {"limits", QJsonObject{{"max_voxels", m_limit->value() * 1000000}}},
        {"cloudcompare", QJsonObject{{"global_shift", QJsonArray{shift.x, shift.y, shift.z}}, {"global_scale", m_cloud->getGlobalScale()}, {"export_coordinates", "GLOBAL"}}}
    };
    requestPath = m_work->filePath("request.json");
    QFile request(requestPath);
    const auto bytes = QJsonDocument(job).toJson();
    if (!request.open(QIODevice::WriteOnly) || request.write(bytes) != bytes.size())
        throw std::runtime_error("Scrittura del job fallita.");
    return true;
}

void FuzzyDialog::start()
{
    m_cancelled = false;
    m_reportedPath.clear();
    m_pending.clear();
    setBusy(true);
    try {
        QString requestPath;
        if (!prepareJob(requestPath)) { setBusy(false); m_work.reset(); return; }
        QSettings settings;
        settings.setValue("qFuzzyRESF/python", m_python->text().trimmed());
        settings.setValue("qFuzzyRESF/units", m_units->currentIndex());
        settings.setValue("qFuzzyRESF/outputDir", QFileInfo(m_output->text()).absolutePath());
        m_progress->setValue(0);
        log(tr("Avvio motore locale. Le dipendenze devono essere installate nell'interprete selezionato."));
        m_process.setWorkingDirectory(m_work->path());
        auto environment = QProcessEnvironment::systemEnvironment();
        environment.insert("PYTHONUTF8", "1");
        environment.insert("PYTHONUNBUFFERED", "1");
        m_process.setProcessEnvironment(environment);
        m_process.start(m_python->text().trimmed(), {m_work->filePath("run_pipeline.py"), "--request", requestPath});
    } catch (const std::exception& error) {
        log(QString::fromUtf8(error.what()));
        setBusy(false);
        m_work.reset();
    }
}

void FuzzyDialog::readOutput()
{
    m_pending += m_process.readAllStandardOutput();
    while (m_pending.contains('\n')) {
        const auto end = m_pending.indexOf('\n');
        const auto line = m_pending.left(end);
        m_pending.remove(0, end + 1);
        const auto event = QJsonDocument::fromJson(line).object();
        const auto type = event.value("event").toString();
        if (type == "progress") {
            m_progress->setValue(event.value("percent").toInt());
            m_progress->setFormat(event.value("stage").toString() + " · %p%");
        } else if (type == "result") {
            m_reportedPath = event.value("output").toString();
            const auto counts = event.value("counts").toObject();
            log(tr("IFC validato: %1 pareti, %2 spazi provvisori.\nReport: %3")
                .arg(counts.value("IfcWall").toInt()).arg(counts.value("IfcSpace").toInt())
                .arg(event.value("diagnostics").toString()));
        } else if (event.contains("message")) {
            log(event.value("message").toString());
        } else if (!line.trimmed().isEmpty()) {
            log(QString::fromUtf8(line).left(2048));
        }
    }
    if (m_pending.size() > 1024 * 1024) m_pending.clear();
}

void FuzzyDialog::finished(int code, QProcess::ExitStatus status)
{
    readOutput();
    if (code == 0 && status == QProcess::NormalExit && QFileInfo::exists(m_reportedPath)) {
        m_resultPath = m_reportedPath;
        log(tr("Salvato: %1").arg(m_resultPath));
        m_progress->setValue(100);
    } else {
        log(m_cancelled ? tr("Elaborazione annullata.") : tr("Elaborazione non completata (codice %1). Consultare il messaggio precedente.").arg(code));
    }
    setBusy(false);
    m_work.reset();
}
