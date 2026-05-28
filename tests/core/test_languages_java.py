"""Unit tests for lib.languages.java — focused on parse_class_metadata."""

from __future__ import annotations

from lib.languages.java import JAVA, parse_class_metadata


def test_metadata_package_and_class():
    src = "package com.example.app;\npublic class UserService {}\n"
    m = parse_class_metadata(src)
    assert m['package'] == 'com.example.app'
    assert m['class_name'] == 'UserService'
    assert m['extends'] is None
    assert m['implements'] == []


def test_metadata_extends_and_implements():
    src = (
        "package com.example.app;\n"
        "public class UserService extends BaseService implements Foo, Bar {}\n"
    )
    m = parse_class_metadata(src)
    assert m['class_name'] == 'UserService'
    assert m['extends'] == 'BaseService'
    assert m['implements'] == ['Foo', 'Bar']


def test_metadata_interface():
    src = "package x;\npublic interface UserRepository extends BaseRepository<User, Long> {}\n"
    m = parse_class_metadata(src)
    assert m['class_name'] == 'UserRepository'
    assert m['extends'] == 'BaseRepository<User, Long>'


def test_metadata_returns_skeleton_for_empty_content():
    m = parse_class_metadata('')
    assert m == {
        'class_name': None,
        'package': None,
        'extends': None,
        'implements': [],
    }


def test_java_language_exposes_parse_class_metadata():
    assert JAVA.parse_class_metadata is parse_class_metadata
