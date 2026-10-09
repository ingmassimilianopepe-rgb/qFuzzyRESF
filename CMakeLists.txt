option( PLUGIN_STANDARD_QFUZZYRESF "Install qFuzzyRESF plugin" OFF )

if( PLUGIN_STANDARD_QFUZZYRESF )
    project( QFUZZYRESF_PLUGIN )

    AddPlugin( NAME ${PROJECT_NAME} )

    add_subdirectory( include )
    add_subdirectory( src )
    add_subdirectory( resources )
endif()
