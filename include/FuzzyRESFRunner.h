#pragma once

#include "FuzzyRESFDialog.h"

#include <QJsonObject>
#include <QString>

class ccPointCloud;

class FuzzyRESFRunner
{
public:
    struct Result
    {
        bool success = false;
        QString outputDirectory;
        QString resultJsonPath;
        QString ifcPath;
        QString errorMessage;
        QJsonObject summary;
    };

    static Result run(ccPointCloud* cloud,
                      const FuzzyRESFParameters& parameters,
                      const QString& backendDirectory,
                      const QString& temporaryRoot);
};
