// SPDX-License-Identifier: GPL-3.0-or-later
#include "qFuzzyRESF.h"
#include "FuzzyDialog.h"
#include <ccMainAppInterface.h>
#include <ccPointCloud.h>
#include <QAction>
#include <QMainWindow>

qFuzzyRESF::qFuzzyRESF(QObject* parent)
    : QObject(parent), ccStdPluginInterface(":/CC/plugin/qFuzzyRESF/info.json") {}

void qFuzzyRESF::onNewSelection(const ccHObject::Container& selected)
{
    if (m_action)
        m_action->setEnabled(selected.size() == 1 && selected.front()->isA(CC_TYPES::POINT_CLOUD));
}

QList<QAction*> qFuzzyRESF::getActions()
{
    if (!m_action) {
        m_action = new QAction(getIcon(), tr("qFuzzyRESF: genera BIM IFC…"), this);
        m_action->setToolTip(tr("Ricostruisce pareti e spazi provvisori dalla nuvola selezionata"));
        m_action->setEnabled(false);
        connect(m_action, &QAction::triggered, this, &qFuzzyRESF::run);
    }
    return {m_action};
}

void qFuzzyRESF::run()
{
    if (!m_app) return;
    const auto& selected = m_app->getSelectedEntities();
    if (selected.size() != 1 || !selected.front()->isA(CC_TYPES::POINT_CLOUD)) return;
    auto* cloud = static_cast<ccPointCloud*>(selected.front());
    FuzzyDialog dialog(cloud, m_app->getMainWindow());
    dialog.exec();
    if (!dialog.resultPath().isEmpty())
        m_app->dispToConsole(tr("[qFuzzyRESF] IFC salvato: %1 — ambienti provvisori da verificare").arg(dialog.resultPath()));
}
