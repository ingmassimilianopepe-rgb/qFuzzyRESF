// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <ccStdPluginInterface.h>

class qFuzzyRESF final : public QObject, public ccStdPluginInterface
{
    Q_OBJECT
    Q_INTERFACES(ccPluginInterface ccStdPluginInterface)
    Q_PLUGIN_METADATA(IID "cccorp.cloudcompare.plugin.qFuzzyRESF" FILE "../info.json")
public:
    explicit qFuzzyRESF(QObject* parent = nullptr);
    void onNewSelection(const ccHObject::Container& selected) override;
    QList<QAction*> getActions() override;
private:
    QAction* m_action = nullptr;
    void run();
};
