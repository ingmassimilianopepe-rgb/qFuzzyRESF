#include "FuzzyRESFDialog.h"

#include <QCheckBox>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QDoubleSpinBox>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QSpinBox>
#include <QVBoxLayout>

FuzzyRESFDialog::FuzzyRESFDialog(QWidget* parent)
    : QDialog(parent)
{
    setWindowTitle(tr("Fuzzy-RESF Scan-to-BIM"));
    resize(710, 840);

    auto* root = new QVBoxLayout(this);

    auto* intro = new QLabel(
        tr("Fuzzy-RESF-BIM 7.2 restores the evidence-graph-preserving geometry that produced the preferred intermediate BIM before the v7 object-layer regressions.\n"
           "The v6.5 scan-supported wall graph is again authoritative: walls are not re-promoted or re-merged by a second v7 pass, rooms remain derived from that same exported graph, and roof facets retain the connected upper-envelope support model. v7.0/v7.1 remain available only as explicit ablation presets."),
        this);
    intro->setWordWrap(true);
    root->addWidget(intro);

    auto* executionBox = new QGroupBox(tr("Execution"), this);
    auto* executionForm = new QFormLayout(executionBox);
    m_preset = new QComboBox(executionBox);
    m_preset->addItems({"Fuzzy-RESF-BIM 7.2 (Restored evidence graph)",
                        "Fuzzy-RESF-BIM 7.1 (Evidence object hypotheses)",
                        "Fuzzy-RESF-BIM 7.0 (Paper core + object abacus)",
                        "Fuzzy-RESF-BIM 6.5 (Evidence graph + supported roof + stairs)",
                        "Fuzzy-RESF-BIM 6.4 (Architectural primitives + stairs)",
                        "Fuzzy-RESF-BIM 6.3 (Room + roof + wall regularization)",
                        "Fuzzy-RESF-BIM 6.2 (Component-aware historic building envelope)",
                        "Fuzzy-RESF-BIM 6.1 (Robust historic building envelope)",
                        "Fuzzy-RESF-BIM 6.0 (Adaptive building envelope)",
                        "Fuzzy-RESF-BIM 5.1 (Persistent architectural graph)",
                        "Fuzzy-RESF-BIM 4.1 (Paper-aligned object graph)",
                        "Fuzzy-RESF-BIM 3.1 (Topology completion)",
                        "Fuzzy-RESF",
                        "RESF-Max",
                        "RESF-Multi",
                        "RESF-Multi + Topology",
                        "RESF-Multi + Topology + HardRules",
                        "Fuzzy-RESF + Semantic evidence"});
    m_preset->setCurrentText("Fuzzy-RESF-BIM 7.2 (Restored evidence graph)");
    m_python = new QLineEdit("python", executionBox);
    executionForm->addRow(tr("Preset"), m_preset);
    executionForm->addRow(tr("Python executable"), m_python);
    root->addWidget(executionBox);

    auto* resfBox = new QGroupBox(tr("RESF parameters"), this);
    auto* resfForm = new QFormLayout(resfBox);
    m_planeSpacing = new QDoubleSpinBox(resfBox); m_planeSpacing->setRange(0.001, 1.0); m_planeSpacing->setDecimals(3); m_planeSpacing->setValue(0.01); m_planeSpacing->setSuffix(" m");
    m_planeTolerance = new QDoubleSpinBox(resfBox); m_planeTolerance->setRange(0.001, 1.0); m_planeTolerance->setDecimals(3); m_planeTolerance->setValue(0.03); m_planeTolerance->setSuffix(" m");
    m_rasterCell = new QDoubleSpinBox(resfBox); m_rasterCell->setRange(0.005, 1.0); m_rasterCell->setDecimals(3); m_rasterCell->setValue(0.05); m_rasterCell->setSuffix(" m");
    m_angleStep = new QDoubleSpinBox(resfBox); m_angleStep->setRange(0.25, 30.0); m_angleStep->setDecimals(2); m_angleStep->setValue(2.0); m_angleStep->setSuffix(" deg");
    m_maxOrientations = new QSpinBox(resfBox); m_maxOrientations->setRange(1, 36); m_maxOrientations->setValue(12);
    m_confidence = new QDoubleSpinBox(resfBox); m_confidence->setRange(0.0, 1.0); m_confidence->setDecimals(2); m_confidence->setSingleStep(0.05); m_confidence->setValue(0.50);
    resfForm->addRow(tr("Plane spacing Δd"), m_planeSpacing);
    resfForm->addRow(tr("Point-plane tolerance ε"), m_planeTolerance);
    resfForm->addRow(tr("Projected support grid"), m_rasterCell);
    resfForm->addRow(tr("Angular step Δθ"), m_angleStep);
    resfForm->addRow(tr("Max. orientation families"), m_maxOrientations);
    resfForm->addRow(tr("Fuzzy acceptance threshold"), m_confidence);
    root->addWidget(resfBox);

    auto* reasoningBox = new QGroupBox(tr("Reasoning and object reconstruction"), this);
    auto* reasoningLayout = new QVBoxLayout(reasoningBox);
    m_multiPeak = new QCheckBox(tr("Multi-peak RESF"), reasoningBox);
    m_topology = new QCheckBox(tr("Topology / room + facade graph continuity"), reasoningBox);
    m_occlusion = new QCheckBox(tr("Occlusion reasoning"), reasoningBox);
    m_fuzzy = new QCheckBox(tr("Fuzzy evidence fusion"), reasoningBox);
    m_instance = new QCheckBox(tr("Physical BIM wall/object consolidation"), reasoningBox);
    m_feedback = new QCheckBox(tr("Cloud↔BIM geometric feedback"), reasoningBox);
    for (QCheckBox* cb : {m_multiPeak, m_topology, m_occlusion, m_fuzzy, m_instance, m_feedback})
        reasoningLayout->addWidget(cb);
    root->addWidget(reasoningBox);

    auto* outputBox = new QGroupBox(tr("Output"), this);
    auto* outputLayout = new QVBoxLayout(outputBox);
    m_exportIFC = new QCheckBox(tr("Export semantic IFC (walls/openings/floors/sloped roofs/columns/spaces/stairs)"), outputBox);
    m_exportDiagnostics = new QCheckBox(tr("Export CSV/JSON diagnostics including RESF candidates and restoration policy"), outputBox);
    m_exportIFC->setChecked(true);
    m_exportDiagnostics->setChecked(true);
    outputLayout->addWidget(m_exportIFC);
    outputLayout->addWidget(m_exportDiagnostics);
    root->addWidget(outputBox);

    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    connect(buttons, &QDialogButtonBox::accepted, this, &FuzzyRESFDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &FuzzyRESFDialog::reject);
    root->addWidget(buttons);

    connect(m_preset, &QComboBox::currentTextChanged, this, &FuzzyRESFDialog::applyPreset);
    applyPreset(m_preset->currentText());
}

void FuzzyRESFDialog::applyPreset(const QString& preset)
{
    const bool isBimV72 = preset == "Fuzzy-RESF-BIM 7.2 (Restored evidence graph)";
    const bool isBimV71 = preset == "Fuzzy-RESF-BIM 7.1 (Evidence object hypotheses)";
    const bool isBimV70 = preset == "Fuzzy-RESF-BIM 7.0 (Paper core + object abacus)";
    const bool isBimV65 = preset == "Fuzzy-RESF-BIM 6.5 (Evidence graph + supported roof + stairs)";
    const bool isBimV64 = preset == "Fuzzy-RESF-BIM 6.4 (Architectural primitives + stairs)";
    const bool isBimV63 = preset == "Fuzzy-RESF-BIM 6.3 (Room + roof + wall regularization)";
    const bool isBimV62 = preset == "Fuzzy-RESF-BIM 6.2 (Component-aware historic building envelope)";
    const bool isBimV61 = preset == "Fuzzy-RESF-BIM 6.1 (Robust historic building envelope)";
    const bool isBimV60 = preset == "Fuzzy-RESF-BIM 6.0 (Adaptive building envelope)";
    const bool isBimV51 = preset == "Fuzzy-RESF-BIM 5.1 (Persistent architectural graph)";
    const bool isBimV41 = preset == "Fuzzy-RESF-BIM 4.1 (Paper-aligned object graph)";
    const bool isBimV31 = preset == "Fuzzy-RESF-BIM 3.1 (Topology completion)";
    const bool isBim = isBimV72 || isBimV71 || isBimV70 || isBimV65 || isBimV64 || isBimV63 || isBimV62 || isBimV61 || isBimV60 || isBimV51 || isBimV41 || isBimV31;
    const bool isMax = preset == "RESF-Max";
    const bool isMulti = !isMax;
    const bool hasTopology = isBim || preset.contains("Topology") || preset.startsWith("Fuzzy-RESF");
    const bool isHardRules = preset.contains("HardRules");
    const bool isFuzzy = isBim || preset.startsWith("Fuzzy-RESF");

    m_multiPeak->setChecked(isMulti);
    m_topology->setChecked(hasTopology);
    m_occlusion->setChecked(hasTopology);
    m_fuzzy->setChecked(isFuzzy && !isHardRules);
    m_instance->setChecked(isBim || isFuzzy || isHardRules);
    m_feedback->setChecked(isBim || isFuzzy);

    // Preserve the manuscript frozen RESF sampling/decision settings. v7.2 restores the
    // proven v6.5 object graph and does not retune the paper core.
    if (isBim)
    {
        m_planeSpacing->setValue(0.01);
        m_planeTolerance->setValue(0.03);
        m_rasterCell->setValue(0.05);
        m_confidence->setValue(0.50);
        m_angleStep->setValue(2.0);
        m_maxOrientations->setValue(12);
    }
}

FuzzyRESFParameters FuzzyRESFDialog::parameters() const
{
    FuzzyRESFParameters p;
    p.preset = m_preset->currentText();
    p.pythonExecutable = m_python->text().trimmed();
    p.planeSpacing = m_planeSpacing->value();
    p.planeTolerance = m_planeTolerance->value();
    p.rasterCell = m_rasterCell->value();
    p.angleStepDeg = m_angleStep->value();
    p.confidenceThreshold = m_confidence->value();
    p.maxOrientationFamilies = m_maxOrientations->value();
    p.multiPeak = m_multiPeak->isChecked();
    p.topology = m_topology->isChecked();
    p.occlusion = m_occlusion->isChecked();
    p.fuzzy = m_fuzzy->isChecked();
    p.instanceConsolidation = m_instance->isChecked();
    p.geometricFeedback = m_feedback->isChecked();
    p.exportIFC = m_exportIFC->isChecked();
    p.exportDiagnostics = m_exportDiagnostics->isChecked();
    return p;
}
