#!/usr/bin/env bash

export GALAXY_TEST_EXTERNAL="http://34.74.57.92:8000"
export GALAXY_TEST_USER_EMAIL="alex@fake.org"
export GALAXY_TEST_SELENIUM_ADMIN_USER_EMAIL="alex@fake.org"
export GALAXY_TEST_SELENIUM_ADMIN_USER_PASSWORD="password"
export GALAXY_TEST_SELENIUM_USER_EMAIL="alex@fake.org"
export GALAXY_TEST_SELENIUM_USER_PASSWORD="password"

FILE_LIST=""

if [[ $# -gt 0 ]] ; then
	while [[ $# -gt 0 ]] ; do
		case $1 in
			-f|--file)
				FILE_LIST=$(cat $2)
				shift 2
				;;
			*)
				FILE_LIST="$FILE_LIST $1"
				shift
				;;			
		esac
	done
else
	FILE_LIST=$(cat single_user_selenium_tests.txt)
fi

[ -e database/test_errors ] || mkdir database/test_errors
for test in $FILE_LIST ; do
	./run_tests.sh -selenium lib/galaxy_test/selenium/$test
	if [ -e run_selenium_tests.html ] ; then
		html=$(echo $test | sed 's/.py/.html/')
		mv run_selenium_tests.html database/test_errors/$html
	fi
done

