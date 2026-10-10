#include "FuzzyRESFRunner.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QProcess>
#include <QTextStream>

#include <ccPointCloud.h>

FuzzyRESFRunner::Result FuzzyRESFRunner::run(ccPointCloud* cloud,
                                             const FuzzyRESFParameters& p,
                                             const QString& backendDirectory,
                                             const QString& temporaryRoot)
{
    Result result;
    if (!cloud)
    {
        result.errorMessage = "No input cloud was provided.";
        return result;
    }

    QDir().mkpath(temporaryRoot);
    const QString runId = QDateTime::currentDateTimeUtc().toString("yyyyMMdd_hhmmss_zzz");
    result.outputDirectory = QDir(temporaryRoot).filePath("run_" + runId);
    if (!QDir().mkpath(result.outputDirectory))
    {
        result.errorMessage = "Unable to create the Fuzzy-RESF working directory.";
        return result;
    }

    const QString xyzPath = QDir(result.outputDirectory).filePath("input.xyz");
    QFile xyzFile(xyzPath);
    if (!xyzFile.open(QIODevice::WriteOnly | QIODevice::Text))
    {
        result.errorMessage = "Unable to export the selected cloud to the temporary XYZ file.";
        return result;
    }

    QTextStream xyz(&xyzFile);
    xyz.setRealNumberNotation(QTextStream::SmartNotation);
    xyz.setRealNumberPrecision(12);
    for (unsigned i = 0; i < cloud->size(); ++i)
    {
        const CCVector3* P = cloud->getPoint(i);
        xyz << P->x << ' ' << P->y << ' ' << P->z << '\n';
    }
    xyzFile.close();

    QJsonObject request;
    request["method"] = "Fuzzy-RESF-BIM";
    request["version"] = "3.0-semantic-topological";
    request["preset"] = p.preset;
    request["input"] = xyzPath;
    request["output"] = result.outputDirectory;
    request["plane_spacing_m"] = p.planeSpacing;
    request["plane_tolerance_m"] = p.planeTolerance;
    request["raster_cell_m"] = p.rasterCell;
    request["angle_step_deg"] = p.angleStepDeg;
    request["confidence_threshold"] = p.confidenceThreshold;
    request["max_orientation_families"] = p.maxOrientationFamilies;
    request["max_points"] = 2000000;
    request["multi_peak"] = p.multiPeak;
    request["topology"] = p.topology;
    request["occlusion"] = p.occlusion;
    request["fuzzy"] = p.fuzzy;
    request["instance_consolidation"] = p.instanceConsolidation;
    request["geometric_feedback"] = p.geometricFeedback;
    request["export_ifc"] = p.exportIFC;
    request["export_diagnostics"] = p.exportDiagnostics;

    const QString requestPath = QDir(result.outputDirectory).filePath("request.json");
    QFile requestFile(requestPath);
    if (!requestFile.open(QIODevice::WriteOnly | QIODevice::Text))
    {
        result.errorMessage = "Unable to write the backend request JSON.";
        return result;
    }
    requestFile.write(QJsonDocument(request).toJson(QJsonDocument::Indented));
    requestFile.close();

    const QString cliPath = QDir(backendDirectory).filePath("fuzzy_resf_cli.py");
    if (!QFile::exists(cliPath))
    {
        result.errorMessage = QString("Fuzzy-RESF backend not found: %1\n"
                                      "Set QFUZZYRESF_BACKEND_DIR to the repository backend directory.").arg(cliPath);
        return result;
    }

    QProcess process;
    process.setWorkingDirectory(backendDirectory);
    process.start(p.pythonExecutable, {cliPath, "--request", requestPath});
    if (!process.waitForStarted(10000))
    {
        result.errorMessage = "Unable to start the configured Python interpreter.";
        return result;
    }
    if (!process.waitForFinished(-1))
    {
        process.kill();
        result.errorMessage = "Fuzzy-RESF backend execution did not finish correctly.";
        return result;
    }

    if (process.exitStatus() != QProcess::NormalExit || process.exitCode() != 0)
    {
        result.errorMessage = QString("Fuzzy-RESF backend failed.\n\n%1")
                                  .arg(QString::fromUtf8(process.readAllStandardError()));
        return result;
    }

    result.resultJsonPath = QDir(result.outputDirectory).filePath("result.json");
    QFile resultFile(result.resultJsonPath);
    if (!resultFile.open(QIODevice::ReadOnly | QIODevice::Text))
    {
        result.errorMessage = "The backend finished but did not produce result.json.";
        return result;
    }

    const QJsonDocument resultDocument = QJsonDocument::fromJson(resultFile.readAll());
    result.summary = resultDocument.object();
    result.ifcPath = QDir(result.outputDirectory).filePath("fuzzy_resf.ifc");
    result.success = result.summary.value("status").toString() == "success";
    if (!result.success)
        result.errorMessage = result.summary.value("error").toString("Unknown backend error.");
    return result;
}
