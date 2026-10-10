#include "FuzzyRESFResultImporter.h"

#include <QColor>
#include <QDir>
#include <QFile>
#include <QHash>
#include <QStringList>
#include <QTextStream>

#include <ccBox.h>
#include <ccGLMatrix.h>
#include <ccHObject.h>
#include <ccMainAppInterface.h>
#include <ccPointCloud.h>

#include <algorithm>
#include <cmath>

namespace
{
struct WallGeometry
{
    double thetaDeg = 0.0;
    double d = 0.0;
    double x1 = 0.0;
    double y1 = 0.0;
    double x2 = 0.0;
    double y2 = 0.0;
    double z0 = 0.0;
    double z1 = 0.0;
    double thickness = 0.18;
};

QColor colorForState(const QString& state)
{
    const QString s = state.trimmed().toLower();
    if (s == "permanent") return QColor(44, 160, 44);
    if (s == "occludedpermanent" || s == "occluded_permanent") return QColor(255, 127, 14);
    if (s == "clutter") return QColor(214, 39, 40);
    return QColor(127, 127, 127);
}

ccBox* makeBox(const QString& name,
               double cx,
               double cy,
               double cz,
               double length,
               double depth,
               double height,
               double angleRad,
               const QColor& color,
               bool wireframe)
{
    if (length <= 0.0 || depth <= 0.0 || height <= 0.0)
        return nullptr;

    ccGLMatrix transform;
    transform.initFromParameters(static_cast<PointCoordinateType>(angleRad),
                                 CCVector3(0, 0, 1),
                                 CCVector3(static_cast<PointCoordinateType>(cx),
                                           static_cast<PointCoordinateType>(cy),
                                           static_cast<PointCoordinateType>(cz)));

    auto* box = new ccBox(CCVector3(static_cast<PointCoordinateType>(length),
                                    static_cast<PointCoordinateType>(depth),
                                    static_cast<PointCoordinateType>(height)),
                          &transform,
                          name);
    box->setColor(ccColor::Rgb(static_cast<unsigned char>(color.red()),
                               static_cast<unsigned char>(color.green()),
                               static_cast<unsigned char>(color.blue())));
    box->showColors(true);
    box->showWired(wireframe);
    box->setVisible(true);
    return box;
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
    auto* permanent = new ccHObject("Walls - Permanent");
    auto* occluded = new ccHObject("Walls - OccludedPermanent");
    auto* clutter = new ccHObject("Rejected - Clutter");
    auto* uncertain = new ccHObject("Review - Uncertain");
    auto* doors = new ccHObject("Doors");
    auto* windows = new ccHObject("Windows");
    resultRoot->addChild(permanent);
    resultRoot->addChild(occluded);
    resultRoot->addChild(doors);
    resultRoot->addChild(windows);
    resultRoot->addChild(uncertain);
    resultRoot->addChild(clutter);

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
    const int iid = column("id");
    const int itheta = column("theta_deg");
    const int id = column("d");
    const int ix1 = column("x1");
    const int iy1 = column("y1");
    const int ix2 = column("x2");
    const int iy2 = column("y2");
    const int iz0 = column("z0");
    const int iz1 = column("z1");
    const int ithickness = column("thickness");
    const int istate = column("state");
    const int iconf = column("confidence");

    if (iid < 0 || itheta < 0 || id < 0 || ix1 < 0 || iy1 < 0 || ix2 < 0 || iy2 < 0 ||
        iz0 < 0 || iz1 < 0 || ithickness < 0 || istate < 0)
    {
        errorMessage = "walls.csv does not match the qFuzzyRESF semantic BIM contract.";
        delete resultRoot;
        resultRoot = nullptr;
        return false;
    }

    QHash<int, WallGeometry> wallGeometry;
    int importedWalls = 0;
    while (!stream.atEnd())
    {
        const QString line = stream.readLine().trimmed();
        if (line.isEmpty())
            continue;
        const QStringList fields = line.split(',');
        if (fields.size() < header.size())
            continue;

        const int wallId = fields[iid].toInt();
        WallGeometry g;
        g.thetaDeg = fields[itheta].toDouble();
        g.d = fields[id].toDouble();
        g.x1 = fields[ix1].toDouble();
        g.y1 = fields[iy1].toDouble();
        g.x2 = fields[ix2].toDouble();
        g.y2 = fields[iy2].toDouble();
        g.z0 = fields[iz0].toDouble();
        g.z1 = fields[iz1].toDouble();
        g.thickness = std::max(0.08, fields[ithickness].toDouble());
        wallGeometry.insert(wallId, g);

        const QString state = fields[istate].trimmed();
        const double confidence = iconf >= 0 ? fields[iconf].toDouble() : 0.0;
        const double dx = g.x2 - g.x1;
        const double dy = g.y2 - g.y1;
        const double length = std::hypot(dx, dy);
        const double height = g.z1 - g.z0;
        if (length <= 0.0 || height <= 0.0)
            continue;

        const double cx = 0.5 * (g.x1 + g.x2);
        const double cy = 0.5 * (g.y1 + g.y2);
        const double cz = 0.5 * (g.z0 + g.z1);
        const double angle = std::atan2(dy, dx);
        const QString s = state.toLower();
        const bool wireframe = (s == "clutter" || s == "uncertain");
        auto* box = makeBox(QString("Wall_%1 [%2, conf=%3]").arg(wallId).arg(state).arg(confidence, 0, 'f', 2),
                            cx, cy, cz, length, g.thickness, height, angle, colorForState(state), wireframe);
        if (!box)
            continue;
        box->setMetaData("FuzzyRESF.WallId", wallId);
        box->setMetaData("FuzzyRESF.Confidence", confidence);
        box->setMetaData("FuzzyRESF.State", state);

        ccHObject* parent = uncertain;
        if (s == "permanent") parent = permanent;
        else if (s == "occludedpermanent" || s == "occluded_permanent") parent = occluded;
        else if (s == "clutter") parent = clutter;
        parent->addChild(box);
        ++importedWalls;
    }

    // Semantic openings produced by the v1.1 backend.
    const QString openingsPath = QDir(outputDirectory).filePath("openings.csv");
    QFile openingsFile(openingsPath);
    int importedDoors = 0;
    int importedWindows = 0;
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
            const int os0 = ocol("s0");
            const int os1 = ocol("s1");
            const int oz0 = ocol("z0");
            const int oz1 = ocol("z1");
            const int oconf = ocol("confidence");

            while (!os.atEnd())
            {
                const QString line = os.readLine().trimmed();
                if (line.isEmpty())
                    continue;
                const QStringList fields = line.split(',');
                if (fields.size() < oh.size() || oid < 0 || owall < 0 || okind < 0 || os0 < 0 || os1 < 0 || oz0 < 0 || oz1 < 0)
                    continue;

                const int openingId = fields[oid].toInt();
                const int wallId = fields[owall].toInt();
                if (!wallGeometry.contains(wallId))
                    continue;
                const WallGeometry g = wallGeometry.value(wallId);
                const QString kind = fields[okind].trimmed();
                const double s0 = fields[os0].toDouble();
                const double s1 = fields[os1].toDouble();
                const double z0 = fields[oz0].toDouble();
                const double z1 = fields[oz1].toDouble();
                const double confidence = oconf >= 0 ? fields[oconf].toDouble() : 0.0;
                const double width = s1 - s0;
                const double height = z1 - z0;
                if (width <= 0.0 || height <= 0.0)
                    continue;

                const double theta = g.thetaDeg * 3.14159265358979323846 / 180.0;
                const double nx = std::cos(theta);
                const double ny = std::sin(theta);
                const double tx = -std::sin(theta);
                const double ty = std::cos(theta);
                const double tA = g.x1 * tx + g.y1 * ty;
                const double tB = g.x2 * tx + g.y2 * ty;
                const double centerT = std::min(tA, tB) + 0.5 * (s0 + s1);

                // Offset the preview slightly outside the wall so the opening object
                // remains visible in CloudCompare even though the host wall is shown
                // as a solid box. The IFC itself contains a real IfcOpeningElement.
                const double previewOffset = 0.5 * g.thickness + 0.03;
                const double cx = nx * g.d + tx * centerT + nx * previewOffset;
                const double cy = ny * g.d + ty * centerT + ny * previewOffset;
                const double cz = 0.5 * (z0 + z1);
                const double angle = std::atan2(ty, tx);
                const bool isDoor = kind.compare("Door", Qt::CaseInsensitive) == 0;
                const QColor color = isDoor ? QColor(166, 110, 66) : QColor(31, 119, 180);
                auto* box = makeBox(QString("%1_%2 [wall=%3, conf=%4]").arg(kind).arg(openingId).arg(wallId).arg(confidence, 0, 'f', 2),
                                    cx, cy, cz, width, std::max(0.05, g.thickness * 0.65), height, angle, color, false);
                if (!box)
                    continue;
                box->setMetaData("FuzzyRESF.HostWallId", wallId);
                box->setMetaData("FuzzyRESF.Confidence", confidence);
                if (isDoor)
                {
                    doors->addChild(box);
                    ++importedDoors;
                }
                else
                {
                    windows->addChild(box);
                    ++importedWindows;
                }
            }
        }
    }

    resultRoot->setMetaData("FuzzyRESF.OutputDirectory", outputDirectory);
    resultRoot->setMetaData("FuzzyRESF.IFC", QDir(outputDirectory).filePath("fuzzy_resf.ifc"));
    resultRoot->setMetaData("FuzzyRESF.Walls", importedWalls);
    resultRoot->setMetaData("FuzzyRESF.Doors", importedDoors);
    resultRoot->setMetaData("FuzzyRESF.Windows", importedWindows);
    app->addToDB(resultRoot);
    return true;
}
