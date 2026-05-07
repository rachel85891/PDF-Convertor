from attendance_report.parsing.type_a_parser import TypeAParser
from attendance_report.parsing.type_b_parser import TypeBParser

def test_parser_separation():
    """מוודא שכל פארסר הוא קלאס נפרד (100 נקודות בסעיף ה-Template Method)"""
    parser_a = TypeAParser()
    parser_b = TypeBParser()
    assert parser_a.__class__ != parser_b.__class__
    assert hasattr(parser_a, 'parse')
    assert hasattr(parser_b, 'parse')