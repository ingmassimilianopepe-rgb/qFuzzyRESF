#pragma once

#include "ccStdPluginInterface.h"

class ccCommandLineInterface;

class qFuzzyRESF : public QObject, public ccStdPluginInterface
{
    Q_OBJECT
    Q_INTERFACES( ccPluginInterface ccStdPluginInterface )
    Q_PLUGIN_METADATA( IID "cccorp.cloudcompare.plugin.qFuzzyRESF" FILE "../info.json" )

public:
    explicit qFuzzyRESF(QObject* parent = nullptr);
    ~qFuzzyRESF() override = default;

    void onNewSelection(const ccHObject::Container& selectedEntities) override;
    QList<QAction*> getActions() override;
    void registerCommands(ccCommandLineInterface* cmd) override;

private:
    void doAction();

    QAction* m_action = nullptr;
};
