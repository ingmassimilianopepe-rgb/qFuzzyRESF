#pragma once

#include <QDialog>
#include <QString>

class QCheckBox;
class QComboBox;
class QDoubleSpinBox;
class QLineEdit;
class QSpinBox;

struct FuzzyRESFParameters
{
    QString preset = "Fuzzy-RESF-BIM 3.0 (Semantic)";
    QString pythonExecutable = "python";
    double planeSpacing = 0.01;
    double planeTolerance = 0.03;
    double rasterCell = 0.05;
    double angleStepDeg = 2.0;
    double confidenceThreshold = 0.50;
    int maxOrientationFamilies = 12;
    bool multiPeak = true;
    bool topology = true;
    bool occlusion = true;
    bool fuzzy = true;
    bool instanceConsolidation = true;
    bool geometricFeedback = true;
    bool exportIFC = true;
    bool exportDiagnostics = true;
};

class FuzzyRESFDialog : public QDialog
{
    Q_OBJECT

public:
    explicit FuzzyRESFDialog(QWidget* parent = nullptr);
    FuzzyRESFParameters parameters() const;

private slots:
    void applyPreset(const QString& preset);

private:
    QComboBox* m_preset = nullptr;
    QLineEdit* m_python = nullptr;
    QDoubleSpinBox* m_planeSpacing = nullptr;
    QDoubleSpinBox* m_planeTolerance = nullptr;
    QDoubleSpinBox* m_rasterCell = nullptr;
    QDoubleSpinBox* m_angleStep = nullptr;
    QDoubleSpinBox* m_confidence = nullptr;
    QSpinBox* m_maxOrientations = nullptr;
    QCheckBox* m_multiPeak = nullptr;
    QCheckBox* m_topology = nullptr;
    QCheckBox* m_occlusion = nullptr;
    QCheckBox* m_fuzzy = nullptr;
    QCheckBox* m_instance = nullptr;
    QCheckBox* m_feedback = nullptr;
    QCheckBox* m_exportIFC = nullptr;
    QCheckBox* m_exportDiagnostics = nullptr;
};
