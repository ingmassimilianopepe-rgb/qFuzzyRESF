#include "FuzzyRESFResultImporter.h"

#include <QColor>
#include <QDir>
#include <QFile>
#include <QStringList>
#include <QTextStream>

#include <ccHObject.h>
#include <ccMainAppInterface.h>
#include <ccPointCloud.h>
#include <ccPolyline.h>

namespace
{
QColor colorForState(const QString& state)
{
    const QString s = state.trimmed().toLower();
    if (s == "permanent") return QColor(44, 160, 44);
    if (s == "occludedpermanent" || s == "occluded_permanent") return QColor(255, 127, 14);
    if (s == "clutter") return QColor(214, 39, 40);
    return QColor(127, 127, 127);
}
}

bool FuzzyRESFResultImporter::importResults(const QString& outputDirectory,
                                            ccPointCloud* sourceCloud,
                                            ccMainAppInterface* app,
                                            ccHObject*& resultRoot,
                                            QString& errorMessage)
{
    if (!sourceCloud || !app)
    {
        errorMessage = "Invalid CloudCompare source cloud or application interface.";
        return false;
    }

    const QString wallsPath = QDir(outputDirectory).filePath("walls.csv");
    QFile wallsFile(wallsPath);
    if (!wallsFile.open(QIODevice::ReadOnly | QIODevice::Text))
    {
        errorMessage = QString("walls.csv not found in %1").arg(outputDirectory);
        return false;
    }

    resultRoot = new ccHObject(sourceCloud->getName() + "_FuzzyRESF");
    auto* permanent = new ccHObject("Permanent");
    auto* occluded = new ccHObject("OccludedPermanent");
    auto* clutter = new ccHObject("Clutter");
    auto* uncertain = new ccHObject("Uncertain");
    resultRoot->addChild(permanent);
    resultRoot->addChild(occluded);
    resultRoot->addChild(clutter);
    resultRoot->addChild(uncertain);

    QTextStream stream(&wallsFile);
    if (stream.atEnd())
    {
        errorMessage = "walls.csv is empty.";
        delete resultRoot;
        resultRoot = nullptr;
        return false;
    }

    const QStringList header = stream.readLine().split(',');
    auto column = [&header](const QString& name) { return header.indexOf(name); };
    const int ix1 = column("x1");
    const int iy1 = column("y1");
    const int iz0 = column("z0");
    const int ix2 = column("x2");
    const int iy2 = column("y2");
    const int iz1 = column("z1");
    const int istate = column("state");
    const int iconf = column("confidence");
    const int iid = column("id");

    if (ix1 < 0 || iy1 < 0 || iz0 < 0 || ix2 < 0 || iy2 < 0 || iz1 < 0 || istate < 0)
    {
        errorMessage = "walls.csv does not match the qFuzzyRESF v0.1 contract.";
        delete resultRoot;
        resultRoot = nullptr;
        return false;
    }

    int rowIndex = 0;
    while (!stream.atEnd())
    {
        const QString line = stream.readLine().trimmed();
        if (line.isEmpty()) continue;
        const QStringList fields = line.split(',');
        if (fields.size() < header.size()) continue;

        const double x1 = fields[ix1].toDouble();
        const double y1 = fields[iy1].toDouble();
        const double z0 = fields[iz0].toDouble();
        const double x2 = fields[ix2].toDouble();
        const double y2 = fields[iy2].toDouble();
        const double z1 = fields[iz1].toDouble();
        const QString state = fields[istate].trimmed();
        const double confidence = iconf >= 0 ? fields[iconf].toDouble() : 0.0;
        const QString id = iid >= 0 ? fields[iid].trimmed() : QString::number(++rowIndex);

        auto* vertices = new ccPointCloud(QString("Wall_%1_vertices").arg(id));
        vertices->reserve(4);
        vertices->addPoint(CCVector3(x1, y1, z0));
        vertices->addPoint(CCVector3(x2, y2, z0));
        vertices->addPoint(CCVector3(x2, y2, z1));
        vertices->addPoint(CCVector3(x1, y1, z1));
        vertices->copyGlobalShiftAndScale(*sourceCloud);

        auto* poly = new ccPolyline(vertices);
        poly->addChild(vertices);
        poly->addPointIndex(0, 4);
        poly->setClosed(true);
        poly->setName(QString("Wall_%1 [%2, conf=%3]").arg(id, state).arg(confidence, 0, 'f', 2));
        const QColor c = colorForState(state);
        poly->setColor(ccColor::Rgb(static_cast<unsigned char>(c.red()),
                                    static_cast<unsigned char>(c.green()),
                                    static_cast<unsigned char>(c.blue())));
        poly->showColors(true);
        poly->setVisible(true);

        ccHObject* parent = uncertain;
        const QString s = state.toLower();
        if (s == "permanent") parent = permanent;
        else if (s == "occludedpermanent" || s == "occluded_permanent") parent = occluded;
        else if (s == "clutter") parent = clutter;
        parent->addChild(poly);
    }

    resultRoot->setMetaData("FuzzyRESF.OutputDirectory", outputDirectory);
    resultRoot->setMetaData("FuzzyRESF.IFC", QDir(outputDirectory).filePath("fuzzy_resf.ifc"));
    app->addToDB(resultRoot);
    return true;
}
