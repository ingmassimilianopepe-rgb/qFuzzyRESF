// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <QDialog>
#include <QProcess>
#include <QTemporaryDir>
#include <memory>
class ccPointCloud;
class QLineEdit;
class QComboBox;
class QSpinBox;
class QProgressBar;
class QPlainTextEdit;
class QPushButton;

class FuzzyDialog final : public QDialog
{
    Q_OBJECT
public:
    FuzzyDialog(ccPointCloud* cloud, QWidget* parent);
    ~FuzzyDialog() override;
    QString resultPath() const { return m_resultPath; }
    void reject() override;
private:
    ccPointCloud* m_cloud;
    QLineEdit *m_python, *m_output;
    QComboBox *m_units, *m_profile, *m_mode;
    QSpinBox* m_limit;
    QProgressBar* m_progress;
    QPlainTextEdit* m_log;
    QPushButton *m_start, *m_cancel;
    QProcess m_process;
    std::unique_ptr<QTemporaryDir> m_work;
    QByteArray m_pending;
    QString m_resultPath, m_reportedPath;
    bool m_busy = false, m_cancelled = false;
    void start();
    void readOutput();
    void finished(int code, QProcess::ExitStatus status);
    void setBusy(bool busy);
    void log(const QString& message);
    bool prepareJob(QString& requestPath);
};
