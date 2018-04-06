#!/bin/bash

coverage run --source=oioswift -a $(which nosetests) -v tests/unit/controllers
coverage run --source=oioswift -a $(which nosetests) -v tests/unit/common/middleware/test_versioned_writes.py:OioVersionedWritesTestCase

./oio-check-version.sh