Install/Uninstall
-----------------
`python setup.py install`
1 Creates egg file in (<name>-<version>-<py-version>.egg) in ./dist and <python_home>/site-packages 
2 Copies dist package to <python_home>/site-packages
3 Generates wrapper script somewhere on PATH so you can just run ‘environmentbase’

`python setup.py develop`
1 Creates local filesystem reference to repo (<name>.egg-link) into <python_home>/site-packages
2 Generates wrapper script like #3 above

`pip uninstall -y <name>`
- Removes *.egg or *.egg-link from <python_home>/site-packages
- Will need to run twice if both exist

* name and version here refer to setup.py::setup(name=<name>, version=<version> … )


Run/Test/Clean
--------------

# After environmentbase is installed (as either *.egg or *.egg-link) you can run in two ways:
`python -m environmentbase [parameters]`
OR
`environmentbase [parameters]`

They basically do the same thing but the second way is simpler
- The first way runs environmentbase.__main__ from the egg archive directly 
- The second runs an auto-generated wrapper script which invokes environmentbase.__main__.main()

# From the source repo you can run the unit test suite from setup.py
`python setup.py test`

# To remove build files
python setup.py clean —-all

Note: *.pyc files will be regenerated in src whenever you run the test suite but as long as they are git ignored it’s not a big deal. You can still remove them with `rm src/**/*.pyc` 