#include "qFuzzyRESF.h"

#include "FuzzyRESFDialog.h"
#include "FuzzyRESFResultImporter.h"
#include "FuzzyRESFRunner.h"

#include <QAction>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QMainWindow>
#include <QMessageBox>
#include <QSaveFile>
#include <QtGlobal>

#include <ccCommandLineInterface.h>
#include <ccHObject.h>
#include <ccMainAppInterface.h>
#include <ccPointCloud.h>

#include <cassert>

namespace
{
struct ValidatedIfc
{
    bool ok = false;
    QByteArray content;
    QString warning;
};

ValidatedIfc validateIfcForUserSave(const QString& ifcPath)
{
    ValidatedIfc validated;
    const QFileInfo info(ifcPath);

    if (!info.exists() || !info.isFile())
    {
        validated.warning = QObject::tr("IFC export was requested, but no IFC file was produced.\n\nExpected file:\n%1")
                                .arg(ifcPath);
        return validated;
    }

    if (info.size() <= 0)
    {
        QFile::remove(ifcPath);
        validated.warning = QObject::tr("The generated IFC file was empty (0 bytes) and has been removed.\n"
                                        "No empty IFC file will be saved.");
        return validated;
    }

    QFile input(ifcPath);
    if (!input.open(QIODevice::ReadOnly))
    {
        validated.warning = QObject::tr("The generated IFC file could not be opened for validation:\n%1")
                                .arg(ifcPath);
        return validated;
    }

    validated.content = input.readAll();
    input.close();

    if (validated.content.trimmed().isEmpty())
    {
        QFile::remove(ifcPath);
        validated.content.clear();
        validated.warning = QObject::tr("The generated IFC file contained no data and has been removed.\n"
                                        "No empty IFC file will be saved.");
        return validated;
    }

    const QByteArray upper = validated.content.toUpper();
    const bool hasWall = upper.contains("IFCWALL(") || upper.contains("IFCWALLSTANDARDCASE(");
    if (!hasWall)
    {
        validated.content.clear();
        validated.warning = QObject::tr("The IFC file was generated, but it contains no wall entities.\n"
                                        "This usually means that Fuzzy-RESF did not reconstruct any walls.\n"
                                        "The IFC will not be offered for saving.");
        return validated;
    }

    validated.ok = true;
    return validated;
}

bool saveValidatedIfc(const QByteArray& content, const QString& destination, QString& error)
{
    if (content.trimmed().isEmpty())
    {
        error = QObject::tr("Refusing to save an empty IFC file.");
        return false;
    }

    QSaveFile output(destination);
    if (!output.open(QIODevice::WriteOnly))
    {
        error = QObject::tr("Unable to create the IFC file:\n%1").arg(destination);
        return false;
    }

    if (output.write(content) != content.size())
    {
        output.cancelWriting();
        error = QObject::tr("The IFC file could not be written completely. No partial file was kept.");
        return false;
    }

    if (!output.commit())
    {
        error = QObject::tr("Unable to finalize the IFC file. No partial file was kept.");
        return false;
    }

    const QFileInfo savedInfo(destination);
    if (!savedInfo.exists() || savedInfo.size() <= 0)
    {
        QFile::remove(destination);
        error = QObject::tr("The saved IFC file was empty and has been removed.");
        return false;
    }

    return true;
}

class FuzzyRESFCommand final : public ccCommandLineInterface::Command
{
public:
    FuzzyRESFCommand()
        : Command("Fuzzy-RESF Scan-to-BIM", "FUZZY_RESF")
    {
    }

    bool process(ccCommandLineInterface& cmd) override
    {
        cmd.print("[Fuzzy-RESF] Starting command-line reconstruction");

        if (cmd.clouds().size() != 1)
        {
            return cmd.error("Fuzzy-RESF currently expects exactly one loaded point cloud.");
        }

        ccPointCloud* cloud = cmd.clouds().front().pc;
        if (!cloud)
        {
            return cmd.error("Fuzzy-RESF received an invalid point cloud.");
        }

        FuzzyRESFParameters parameters;
        const QString pythonFromEnv = qEnvironmentVariable("QFUZZYRESF_PYTHON");
        parameters.pythonExecutable = pythonFromEnv.isEmpty() ? QStringLiteral("python3") : pythonFromEnv;

        QString backendDir = qEnvironmentVariable("QFUZZYRESF_BACKEND_DIR");
        if (backendDir.isEmpty())
        {
            backendDir = QDir(QCoreApplication::applicationDirPath()).filePath("qFuzzyRESF/backend");
        }

        QString outputRoot = qEnvironmentVariable("QFUZZYRESF_OUTPUT_ROOT");
        if (outputRoot.isEmpty())
        {
            outputRoot = QDir::temp().filePath("qFuzzyRESF-cli");
        }

        const auto result = FuzzyRESFRunner::run(cloud, parameters, backendDir, outputRoot);
        if (!result.success)
        {
            return cmd.error(QString("Fuzzy-RESF backend failed: %1").arg(result.errorMessage));
        }

        if (parameters.exportIFC)
        {
            const auto validatedIfc = validateIfcForUserSave(result.ifcPath);
            if (!validatedIfc.ok)
            {
                cmd.print(QString("[Fuzzy-RESF][WARNING] %1").arg(validatedIfc.warning));
            }
        }

        cmd.print(QString("[Fuzzy-RESF] completed; output=%1").arg(result.outputDirectory));
        return true;
    }
};
} // namespace

qFuzzyRESF::qFuzzyRESF(QObject* parent)
    : QObject(parent)
    , ccStdPluginInterface(":/CC/plugin/qFuzzyRESF/info.json")
{
}

void qFuzzyRESF::onNewSelection(const ccHObject::Container& selectedEntities)
{
    if (!m_action)
        return;

    int cloudCount = 0;
    for (ccHObject* entity : selectedEntities)
    {
        if (entity && entity->isA(CC_TYPES::POINT_CLOUD))
            ++cloudCount;
    }
    m_action->setEnabled(cloudCount == 1);
}

QList<QAction*> qFuzzyRESF::getActions()
{
    if (!m_action)
    {
        m_action = new QAction(getName(), this);
        m_action->setToolTip(tr("Run Fuzzy-RESF Scan-to-BIM on the selected point cloud"));
        m_action->setIcon(getIcon());
        connect(m_action, &QAction::triggered, this, &qFuzzyRESF::doAction);
    }
    return {m_action};
}

void qFuzzyRESF::registerCommands(ccCommandLineInterface* cmd)
{
    if (cmd)
    {
        cmd->registerCommand(ccCommandLineInterface::Command::Shared(new FuzzyRESFCommand));
    }
}

void qFuzzyRESF::doAction()
{
    if (!m_app)
    {
        assert(false);
        return;
    }

    const ccHObject::Container& selected = m_app->getSelectedEntities();
    ccPointCloud* cloud = nullptr;
    for (ccHObject* entity : selected)
    {
        if (entity && entity->isA(CC_TYPES::POINT_CLOUD))
        {
            cloud = static_cast<ccPointCloud*>(entity);
            break;
        }
    }

    if (!cloud)
    {
        m_app->dispToConsole("qFuzzyRESF: select exactly one point cloud.", ccMainAppInterface::ERR_CONSOLE_MESSAGE);
        return;
    }

    FuzzyRESFDialog dialog(m_app->getMainWindow());
    if (dialog.exec() != QDialog::Accepted)
        return;

    const FuzzyRESFParameters parameters = dialog.parameters();

    QString backendDir = qEnvironmentVariable("QFUZZYRESF_BACKEND_DIR");
    if (backendDir.isEmpty())
        backendDir = QDir(QCoreApplication::applicationDirPath()).filePath("qFuzzyRESF/backend");

    const QString tempRoot = QDir::temp().filePath("qFuzzyRESF");
    const auto result = FuzzyRESFRunner::run(cloud, parameters, backendDir, tempRoot);

    if (!result.success)
    {
        QMessageBox::critical(m_app->getMainWindow(), tr("Fuzzy-RESF"), result.errorMessage);
        return;
    }

    ccHObject* resultRoot = nullptr;
    QString importError;
    if (!FuzzyRESFResultImporter::importResults(result.outputDirectory, cloud, m_app, resultRoot, importError))
    {
        QMessageBox::warning(m_app->getMainWindow(), tr("Fuzzy-RESF"),
                             tr("The backend completed, but result visualization failed:\n%1").arg(importError));
    }

    if (parameters.exportIFC)
    {
        const auto validatedIfc = validateIfcForUserSave(result.ifcPath);
        if (!validatedIfc.ok)
        {
            QMessageBox::warning(m_app->getMainWindow(), tr("Fuzzy-RESF IFC warning"), validatedIfc.warning);
            m_app->dispToConsole(QString("qFuzzyRESF IFC warning: %1").arg(validatedIfc.warning),
                                 ccMainAppInterface::WRN_CONSOLE_MESSAGE);
        }
        else
        {
            QString suggestedName = cloud->getName();
            if (suggestedName.trimmed().isEmpty())
                suggestedName = QStringLiteral("fuzzy_resf");
            suggestedName += QStringLiteral("_FuzzyRESF.ifc");

            QString savePath = QFileDialog::getSaveFileName(m_app->getMainWindow(),
                                                            tr("Save Fuzzy-RESF IFC"),
                                                            QDir::home().filePath(suggestedName),
                                                            tr("Industry Foundation Classes (*.ifc)"));
            if (!savePath.isEmpty())
            {
                if (!savePath.endsWith(QStringLiteral(".ifc"), Qt::CaseInsensitive))
                    savePath += QStringLiteral(".ifc");

                QString saveError;
                if (!saveValidatedIfc(validatedIfc.content, savePath, saveError))
                {
                    QMessageBox::warning(m_app->getMainWindow(), tr("Fuzzy-RESF IFC warning"), saveError);
                    m_app->dispToConsole(QString("qFuzzyRESF IFC save warning: %1").arg(saveError),
                                         ccMainAppInterface::WRN_CONSOLE_MESSAGE);
                }
                else
                {
                    QMessageBox::information(m_app->getMainWindow(), tr("Fuzzy-RESF"),
                                             tr("IFC saved successfully:\n%1").arg(savePath));
                    m_app->dispToConsole(QString("qFuzzyRESF: IFC saved to %1").arg(savePath),
                                         ccMainAppInterface::STD_CONSOLE_MESSAGE);
                }
            }
            else
            {
                m_app->dispToConsole(QString("qFuzzyRESF: IFC save cancelled. Temporary IFC: %1").arg(result.ifcPath),
                                     ccMainAppInterface::STD_CONSOLE_MESSAGE);
            }
        }
    }

    m_app->dispToConsole(QString("qFuzzyRESF: completed. Output: %1").arg(result.outputDirectory),
                         ccMainAppInterface::STD_CONSOLE_MESSAGE);
    m_app->refreshAll();
}
