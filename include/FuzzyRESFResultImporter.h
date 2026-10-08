#pragma once

#include <QString>

class ccHObject;
class ccPointCloud;
class ccMainAppInterface;

class FuzzyRESFResultImporter
{
public:
    static bool importResults(const QString& outputDirectory,
                              ccPointCloud* sourceCloud,
                              ccMainAppInterface* app,
                              ccHObject*& resultRoot,
                              QString& errorMessage);
};
