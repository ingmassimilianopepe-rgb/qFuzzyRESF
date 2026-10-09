#include "qFuzzyRESF.h"

#include "FuzzyRESFDialog.h"
#include "FuzzyRESFResultImporter.h"
#include "FuzzyRESFRunner.h"

#include <QAction>
#include <QCoreApplication>
#include <QDir>
#include <QMessageBox>
#include <QtGlobal>

#include <ccHObject.h>
#include <ccMainAppInterface.h>
#include <ccPointCloud.h>

#include <cassert>

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

    QString backendDir = qEnvironmentVariable("QFUZZYRESF_BACKEND_DIR");
    if (backendDir.isEmpty())
        backendDir = QDir(QCoreApplication::applicationDirPath()).filePath("qFuzzyRESF/backend");

    const QString tempRoot = QDir::temp().filePath("qFuzzyRESF");
    const auto result = FuzzyRESFRunner::run(cloud, dialog.parameters(), backendDir, tempRoot);

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

    m_app->dispToConsole(QString("qFuzzyRESF: completed. Output: %1").arg(result.outputDirectory),
                         ccMainAppInterface::STD_CONSOLE_MESSAGE);
    m_app->refreshAll();
}
