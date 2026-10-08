target_sources( ${PROJECT_NAME}
    PRIVATE
        ${CMAKE_CURRENT_LIST_DIR}/qFuzzyRESF.h
        ${CMAKE_CURRENT_LIST_DIR}/FuzzyRESFDialog.h
        ${CMAKE_CURRENT_LIST_DIR}/FuzzyRESFRunner.h
        ${CMAKE_CURRENT_LIST_DIR}/FuzzyRESFResultImporter.h
)

target_include_directories( ${PROJECT_NAME}
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)
