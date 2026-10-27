// SPDX-License-Identifier: GPL-3.0-or-later
// Loads the real plugin, checks action selection, and exercises the actual Qt
// dialog -> XYZ64 -> QProcess -> Python -> IFC bridge, without a display.
#include "FuzzyDialog.h"
#include <ccPointCloud.h>
#include <ccStdPluginInterface.h>
#include <QApplication>
#include <QComboBox>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <cmath>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QPluginLoader>
#include <QPushButton>
#include <QTemporaryDir>
#include <QTimer>
#include <iostream>

int main(int argc, char** argv)
{
    QApplication app(argc, argv);
    app.setOrganizationName("qFuzzyRESF-test");
    app.setApplicationName("native-smoke");
    if (argc != 3) return 2;
    QPluginLoader loader(QString::fromLocal8Bit(argv[1]));
    auto* plugin = qobject_cast<ccStdPluginInterface*>(loader.instance());
    if (!plugin) { std::cerr << loader.errorString().toStdString(); return 3; }
    const auto actions = plugin->getActions();
    if (actions.size() != 1 || actions.front()->isEnabled()) return 4;
    ccPointCloud cloud("Two rooms · native test");
    if (!cloud.reserve(140000)) return 5;
    for (int x = 0; x <= 200; ++x)
        for (int y = 0; y <= 125; ++y)
            for (const float z : {0.f, 3.f}) cloud.addPoint(CCVector3(x * .04f, y * .04f, z));
    for (const float x : {0.f, 4.f, 8.f})
        for (int y = 0; y <= 125; ++y)
            for (int z = 0; z <= 75; ++z) cloud.addPoint(CCVector3(x, y * .04f, z * .04f));
    for (const float y : {0.f, 5.f})
        for (int x = 0; x <= 200; ++x)
            for (int z = 0; z <= 75; ++z) cloud.addPoint(CCVector3(x * .04f, y, z * .04f));
    // Local point values are metres; original coordinates are millimetres
    // near a large survey origin. This exercises both shift and scale.
    cloud.setGlobalShift(CCVector3d(-500000000., -4200000000., -100000.));
    cloud.setGlobalScale(.001);
    plugin->onNewSelection({&cloud});
    if (!actions.front()->isEnabled()) return 6;
    plugin->onNewSelection({&cloud, &cloud});
    if (actions.front()->isEnabled()) return 7;
    QTemporaryDir folder;
    if (!folder.isValid()) return 8;
    const auto output = folder.filePath(QString::fromUtf8("BIM città.ifc"));
    FuzzyDialog dialog(&cloud, nullptr);
    dialog.findChild<QLineEdit*>("pythonPath")->setText(QString::fromLocal8Bit(argv[2]));
    dialog.findChild<QLineEdit*>("outputPath")->setText(output);
    dialog.findChild<QComboBox*>("sourceUnits")->setCurrentIndex(2);
    if (dialog.findChild<QComboBox*>("reconstructionMode")) return 18;
    auto* start = dialog.findChild<QPushButton*>("generateIfc");
    int outcome = 9;
    bool started = false;
    QTimer timer;
    QObject::connect(&timer, &QTimer::timeout, [&] {
        if (!started || !start->isEnabled()) return;
        if (!dialog.resultPath().isEmpty() && QFileInfo(dialog.resultPath()).canonicalFilePath() == QFileInfo(output).canonicalFilePath()) outcome = 0;
        else std::cerr << dialog.findChild<QPlainTextEdit*>()->toPlainText().toStdString();
        app.quit();
    });
    timer.start(100);
    QTimer::singleShot(0, [&] { start->click(); started = true; });
    QTimer::singleShot(180000, [&] { std::cerr << "Native bridge timeout"; outcome = 10; app.quit(); });
    dialog.show();
    app.exec();
    if (outcome != 0) return outcome;
    QFile file(folder.filePath(QString::fromUtf8("BIM città.fuzzyresf/summary.json")));
    if (!file.open(QIODevice::ReadOnly)) return 11;
    const auto summary = QJsonDocument::fromJson(file.readAll()).object();
    const auto counts = summary.value("counts").toObject();
    if (counts.value("IfcWall").toInt() != 5 || counts.value("IfcSpace").toInt() != 2) return 12;
    QFile auditFile(folder.filePath(QString::fromUtf8("BIM città.fuzzyresf/audit.json")));
    if (!auditFile.open(QIODevice::ReadOnly)) return 13;
    const auto low = QJsonDocument::fromJson(auditFile.readAll()).object().value("actual_min_m").toArray();
    if (low.size() != 3 || std::abs(low[0].toDouble() - 500000.) > .01
        || std::abs(low[1].toDouble() - 4200000.) > .01
        || std::abs(low[2].toDouble() - 100.) > .01) return 14;
    std::cout << "Plugin loaded; selection and Qt-to-IFC bridge passed: 5 walls, 2 spaces.\n";
    timer.stop();
    ccPointCloud shell("Sloped roof shell");
    if (!shell.reserve(150000)) return 19;
    for (int x = 0; x <= 160; ++x)
        for (int y = 0; y <= 100; ++y) {
            const float xx=x*.05f, yy=y*.05f;
            const float zz=3.f+.375f*(4.f-std::abs(xx-4.f));
            shell.addPoint(CCVector3(xx,yy,zz));
        }
    for (const float y : {0.f,5.f})
        for (int x=0;x<=160;++x)
            for (int z=0;z<=90;++z) {
                const float xx=x*.05f, zz=z*.05f;
                if (zz<=3.f+.375f*(4.f-std::abs(xx-4.f))) shell.addPoint(CCVector3(xx,y,zz));
            }
    for (const float x : {0.f,8.f})
        for (int y=0;y<=100;++y)
            for (int z=0;z<=60;++z) shell.addPoint(CCVector3(x,y*.05f,z*.05f));
    FuzzyDialog exterior(&shell, nullptr);
    const auto extOutput = folder.filePath("Shell.ifc");
    exterior.findChild<QLineEdit*>("pythonPath")->setText(QString::fromLocal8Bit(argv[2]));
    exterior.findChild<QLineEdit*>("outputPath")->setText(extOutput);
    exterior.findChild<QComboBox*>("sourceUnits")->setCurrentIndex(0);
    if (exterior.findChild<QComboBox*>("reconstructionMode")) return 20;
    auto* extStart = exterior.findChild<QPushButton*>("generateIfc");
    bool extStarted = false;
    outcome = 15;
    QTimer extTimer;
    QObject::connect(&extTimer, &QTimer::timeout, [&] {
        if (!extStarted || !extStart->isEnabled()) return;
        if (QFileInfo(exterior.resultPath()).canonicalFilePath() == QFileInfo(extOutput).canonicalFilePath()
            && QFileInfo::exists(extOutput)) outcome = 0;
        else std::cerr << exterior.findChild<QPlainTextEdit*>()->toPlainText().toStdString();
        app.quit();
    });
    extTimer.start(100);
    QTimer::singleShot(0, [&] { extStart->click(); extStarted = true; });
    exterior.show();
    app.exec();
    if (outcome != 0) return outcome;
    QFile extFile(folder.filePath("Shell.fuzzyresf/summary.json"));
    if (!extFile.open(QIODevice::ReadOnly)) return 16;
    const auto extSummary = QJsonDocument::fromJson(extFile.readAll()).object();
    const auto extCounts = extSummary.value("counts").toObject();
    if (extSummary.value("mode").toString() != "unified" || extCounts.value("IfcSpace").toInt() != 0
        || extCounts.value("IfcRoof").toInt() < 1 || extCounts.value("IfcWall").toInt() < 4) return 17;
    std::cout << "Unified shell-to-IFC bridge passed: walls and sloped roof, no unsupported rooms.\n";
    return 0;
}
