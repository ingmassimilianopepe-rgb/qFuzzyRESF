#include "FuzzyRESFResultImporter.h"

#include <QColor>
#include <QDir>
#include <QFile>
#include <QHash>
#include <QStringList>
#include <QTextStream>
#include <QtGlobal>

#include <ccHObject.h>
#include <ccMainAppInterface.h>
#include <ccPointCloud.h>
#include <ccPolyline.h>

#include <cmath>

namespace
{
struct WallViz
{
    double x1 = 0.0;
    double y1 = 0.0;
    double x2 = 0.0;
    double y2 = 0.0;
    double z0 = 0.0;
    double z1 = 0.0;
};

QColor colorForState(const QString& state)
{
    const QString s = state.trimmed().toLower();
    if (s == "permanent") return QColor(44, 160, 44);
    if (s == "occludedpermanent" || s == "occluded_permanent") return QColor(255, 127, 14);
    if (s == "clutter") return QColor(214, 39, 40);
    return QColor(127, 127, 127);
}

ccPolyline* makeRectangle(const QString& name,
                          const CCVector3& a,
                          const CCVector3& b,
                          const CCVector3& c,
                          const CCVector3& d,
                          const QColor& color,
                          ccPointCloud* sourceCloud)
{
    auto* vertices = new ccPointCloud(name + "_vertices");
    vertices->reserve(4);
    vertices->addPoint(a);
    vertices->addPoint(b);
    vertices->addPoint(c);
    vertices->addPoint(d);
    vertices->copyGlobalShiftAndScale(*sourceCloud);

    auto* poly = new ccPolyline(vertices);
    poly->addChild(vertices);
    poly->addPointIndex(0, 4);
    poly->setClosed(true);
    poly->setName(name);
    poly->setColor(ccColor::Rgb(static_cast<unsigned char>(color.red()),
                                static_cast<unsigned char>(color.green()),
                                static_cast<unsigned char>(color.blue())));
    poly->showColors(true);
    poly->setVisible(true);
    return poly;
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

    resultRoot = new ccHObject(sourceCloud->getName() + "_FuzzyRESF_BIM");
    auto* wallsRoot = new ccHObject("Walls");
    auto* permanent = new ccHObject("Permanent");
    auto* occluded = new ccHObject("OccludedPermanent");
    auto* clutter = new ccHObject("Clutter");
    auto* uncertain = new ccHObject("Uncertain");
    auto* doors = new ccHObject("Doors");
    auto* windows = new ccHObject("Windows");
    wallsRoot->addChild(permanent);
    wallsRoot->addChild(occluded);
    wallsRoot->addChild(clutter);
    wallsRoot->addChild(uncertain);
    resultRoot->addChild(wallsRoot);
    resultRoot->addChild(doors);
    resultRoot->addChild(windows);

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

    if (ix1 < 0 || iy1 < 0 || iz0 < 0 || ix2 < 0 || iy2 < 0 || iz1 < 0)
    {
        errorMessage = "walls.csv is missing required wall geometry columns (x1,y1,z0,x2,y2,z1).";
        delete resultRoot;
        resultRoot = nullptr;
        return false;
    }

    QHash<QString, WallViz> wallById;
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
        const QString state = istate >= 0 ? fields[istate].trimmed() : QStringLiteral("Permanent");
        const double confidence = iconf >= 0 ? fields[iconf].toDouble() : 0.0;
        const QString id = iid >= 0 ? fields[iid].trimmed() : QString::number(++rowIndex);

        wallById.insert(id, WallViz{x1, y1, x2, y2, z0, z1});
        auto* poly = makeRectangle(
            QString("Wall_%1 [%2, conf=%3]").arg(id, state).arg(confidence, 0, 'f', 2),
            CCVector3(x1, y1, z0), CCVector3(x2, y2, z0),
            CCVector3(x2, y2, z1), CCVector3(x1, y1, z1),
            colorForState(state), sourceCloud);

        ccHObject* parent = uncertain;
        const QString s = state.toLower();
        if (s == "permanent") parent = permanent;
        else if (s == "occludedpermanent" || s == "occluded_permanent") parent = occluded;
        else if (s == "clutter") parent = clutter;
        parent->addChild(poly);
    }
    wallsFile.close();

    const QString openingsPath = QDir(outputDirectory).filePath("openings.csv");
    QFile openingsFile(openingsPath);
    if (openingsFile.open(QIODevice::ReadOnly | QIODevice::Text))
    {
        QTextStream os(&openingsFile);
        if (!os.atEnd())
        {
            const QStringList oh = os.readLine().split(',');
            auto ocol = [&oh](const QString& name) { return oh.indexOf(name); };
            const int oid = ocol("id");
            const int owall = ocol("wall_id");
            const int okind = ocol("kind");
            const int ooffset = ocol("offset");
            const int owidth = ocol("width");
            const int osill = ocol("sill");
            const int oheight = ocol("height");
            const int oconf = ocol("confidence");

            if (owall >= 0 && okind >= 0 && ooffset >= 0 && owidth >= 0 && osill >= 0 && oheight >= 0)
            {
                int openingIndex = 0;
                while (!os.atEnd())
                {
                    const QString line = os.readLine().trimmed();
                    if (line.isEmpty()) continue;
                    const QStringList f = line.split(',');
                    if (f.size() < oh.size()) continue;
                    const QString wallId = f[owall].trimmed();
                    if (!wallById.contains(wallId)) continue;
                    const WallViz w = wallById.value(wallId);
                    const double dx = w.x2 - w.x1;
                    const double dy = w.y2 - w.y1;
                    const double length = std::hypot(dx, dy);
                    if (length <= 1.0e-9) continue;
                    const double ux = dx / length;
                    const double uy = dy / length;
                    const double offset = f[ooffset].toDouble();
                    const double width = f[owidth].toDouble();
                    const double sill = f[osill].toDouble();
                    const double height = f[oheight].toDouble();
                    if (width <= 0.0 || height <= 0.0) continue;
                    const double start = qBound(0.0, offset - 0.5 * width, length);
                    const double end = qBound(0.0, offset + 0.5 * width, length);
                    if (end <= start) continue;
                    const double xa = w.x1 + ux * start;
                    const double ya = w.y1 + uy * start;
                    const double xb = w.x1 + ux * end;
                    const double yb = w.y1 + uy * end;
                    const double za = w.z0 + sill;
                    const double zb = qMin(w.z1, za + height);
                    if (zb <= za) continue;

                    const QString kind = f[okind].trimmed();
                    const QString id = oid >= 0 ? f[oid].trimmed() : QString::number(++openingIndex);
                    const double confidence = oconf >= 0 ? f[oconf].toDouble() : 0.0;
                    const bool isDoor = kind.compare("Door", Qt::CaseInsensitive) == 0;
                    const QColor color = isDoor ? QColor(31, 119, 180) : QColor(148, 103, 189);
                    auto* poly = makeRectangle(
                        QString("%1_%2 [wall=%3, conf=%4]").arg(kind, id, wallId).arg(confidence, 0, 'f', 2),
                        CCVector3(xa, ya, za), CCVector3(xb, yb, za),
                        CCVector3(xb, yb, zb), CCVector3(xa, ya, zb), color, sourceCloud);
                    (isDoor ? doors : windows)->addChild(poly);
                }
            }
        }
        openingsFile.close();
    }

    resultRoot->setMetaData("FuzzyRESF.OutputDirectory", outputDirectory);
    resultRoot->setMetaData("FuzzyRESF.IFC", QDir(outputDirectory).filePath("fuzzy_resf.ifc"));
    resultRoot->setMetaData("FuzzyRESF.BIMModel", QDir(outputDirectory).filePath("bim_model.json"));
    app->addToDB(resultRoot);
    return true;
}
