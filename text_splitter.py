import regex as re


MAX_HEADING_LENGTH = 7
MAX_HEADING_CONTENT_LENGTH = 200
MAX_HEADING_UNDERLINE_LENGTH = 200
MAX_HTML_HEADING_ATTRIBUTES_LENGTH = 100
MAX_LIST_ITEM_LENGTH = 200
MAX_NESTED_LIST_ITEMS = 6
MAX_LIST_INDENT_SPACES = 7
MAX_BLOCKQUOTE_LINE_LENGTH = 200
MAX_BLOCKQUOTE_LINES = 15
MAX_CODE_BLOCK_LENGTH = 1500
MAX_CODE_LANGUAGE_LENGTH = 20
MAX_INDENTED_CODE_LINES = 20
MAX_TABLE_CELL_LENGTH = 200
MAX_TABLE_ROWS = 20
MAX_HTML_TABLE_LENGTH = 2000
MIN_HORIZONTAL_RULE_LENGTH = 3
MAX_SENTENCE_LENGTH = 400
MAX_QUOTED_TEXT_LENGTH = 300
MAX_PARENTHETICAL_CONTENT_LENGTH = 200
MAX_NESTED_PARENTHESES = 5
MAX_MATH_INLINE_LENGTH = 100
MAX_MATH_BLOCK_LENGTH = 500
MAX_PARAGRAPH_LENGTH = 1000
MAX_STANDALONE_LINE_LENGTH = 800
MAX_HTML_TAG_ATTRIBUTES_LENGTH = 100
MAX_HTML_TAG_CONTENT_LENGTH = 1000
LOOKAHEAD_RANGE = 100


class TextSplitter(object):
    MAX_HEADING_LENGTH = 7
    MAX_HEADING_CONTENT_LENGTH = 200
    MAX_HEADING_UNDERLINE_LENGTH = 200
    MAX_HTML_HEADING_ATTRIBUTES_LENGTH = 100
    MAX_LIST_ITEM_LENGTH = 200
    MAX_NESTED_LIST_ITEMS = 6
    MAX_LIST_INDENT_SPACES = 7
    MAX_BLOCKQUOTE_LINE_LENGTH = 200
    MAX_BLOCKQUOTE_LINES = 15
    MAX_CODE_BLOCK_LENGTH = 1500
    MAX_CODE_LANGUAGE_LENGTH = 20
    MAX_INDENTED_CODE_LINES = 20
    MAX_TABLE_CELL_LENGTH = 200
    MAX_TABLE_ROWS = 20
    MAX_HTML_TABLE_LENGTH = 2000
    MIN_HORIZONTAL_RULE_LENGTH = 3
    MAX_SENTENCE_LENGTH = 400
    MAX_QUOTED_TEXT_LENGTH = 300
    MAX_PARENTHETICAL_CONTENT_LENGTH = 200
    MAX_NESTED_PARENTHESES = 5
    MAX_MATH_INLINE_LENGTH = 100
    MAX_MATH_BLOCK_LENGTH = 500
    MAX_PARAGRAPH_LENGTH = 1000
    MAX_STANDALONE_LINE_LENGTH = 800
    MAX_HTML_TAG_ATTRIBUTES_LENGTH = 100
    MAX_HTML_TAG_CONTENT_LENGTH = 1000
    LOOKAHEAD_RANGE = 100

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key.upper()) and isinstance(value, int):
                setattr(self, key.upper(), value)
        self.chunk_regex = self._compile_chunk_regex()

    def _compile_chunk_regex(self):
        return re.compile(
            r"(" +
            # 1. Headings (Setext-style, Markdown, and HTML-style)
            rf"(?:^(?:[#*=-]{{1,{self.MAX_HEADING_LENGTH}}}|\w[^\r\n]{{0,{self.MAX_HEADING_CONTENT_LENGTH}}}\r?\n[-=]{{2,{self.MAX_HEADING_UNDERLINE_LENGTH}}}|<h[1-6][^>]{{0,{self.MAX_HTML_HEADING_ATTRIBUTES_LENGTH}}}>)[^\r\n]{{1,{self.MAX_HEADING_CONTENT_LENGTH}}}(?:</h[1-6]>)?(?:\r?\n|$))" +
            "|" +
            # 2. Citations
            rf"(?:\[[0-9]+\][^\r\n]{{1,{self.MAX_STANDALONE_LINE_LENGTH}}})" +
            "|" +
            # 3. List items (Adjusted to handle indentation correctly)
            rf"(?:(?:^|\r?\n)[ \t]{{0,3}}(?:[-*+•]|\d{{1,3}}\.\w\.|\[[ xX]\])[ \t]+(?:[^\r\n]{{1,{self.MAX_LIST_ITEM_LENGTH}}})(?:\r?\n[ \t]{{2,}}(?:[^\r\n]{{1,{self.MAX_LIST_ITEM_LENGTH}}}))*)" +
            "|" +
            # 4. Block quotes (Handles nested quotes without chunking)
            rf"(?:(?:^>(?:>|\\s{{2,}}){{0,2}}(?:[^\r\n]{{0,{self.MAX_BLOCKQUOTE_LINE_LENGTH}}})(?:\r?\n[ \t]+[^\r\n]{{0,{self.MAX_BLOCKQUOTE_LINE_LENGTH}}})*?\r?\n?))" +
            "|" +
            # 5. Code blocks
            rf"(?:(?:^|\r?\n)(?:```|~~~)(?:\w{{0,{self.MAX_CODE_LANGUAGE_LENGTH}}})?\r?\n[\s\S]{{0,{self.MAX_CODE_BLOCK_LENGTH}}}?(?:```|~~~)\r?\n?)" +
            rf"|(?:(?:^|\r?\n)(?: {{4}}|\t)[^\r\n]{{0,{self.MAX_LIST_ITEM_LENGTH}}}(?:\r?\n(?: {{4}}|\t)[^\r\n]{{0,{self.MAX_LIST_ITEM_LENGTH}}}){{0,{self.MAX_INDENTED_CODE_LINES}}}\r?\n?)" +
            rf"|(?:<pre>(?:<code>)[\s\S]{{0,{self.MAX_CODE_BLOCK_LENGTH}}}?(?:</code>)?</pre>)" +
            "|" +
            # 6. Tables
            rf"(?:(?:^|\r?\n)\|[^\r\n]{{0,{self.MAX_TABLE_CELL_LENGTH}}}\|(?:\r?\n\|[-:]{{1,{self.MAX_TABLE_CELL_LENGTH}}}\|)?(?:\r?\n\|[^\r\n]{{0,{self.MAX_TABLE_CELL_LENGTH}}}\|){{0,{self.MAX_TABLE_ROWS}}})" +
            rf"|<table>[\s\S]{{0,{self.MAX_HTML_TABLE_LENGTH}}}?</table>" +
            "|" +
            # 7. Horizontal rules
            rf"(?:^(?:[-*_]){{{self.MIN_HORIZONTAL_RULE_LENGTH},}}\s*$|<hr\s*/?>)" +
            "|" +
            # 8. Standalone lines or phrases (Prevent chunking by treating indented lines as part of the same block)
            rf"(?:^(?:<[a-zA-Z][^>]{{0,{self.MAX_HTML_TAG_ATTRIBUTES_LENGTH}}}>[^\r\n]{{1,{self.MAX_STANDALONE_LINE_LENGTH}}}(?:[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}})?(?:</[a-zA-Z]+>)?(?:\r?\n|$))" +
            rf"(?:\r?\n[ \t]+[^\r\n]*)*)" +
            "|" +
            # 9. Sentences (Allow sentences to include multiple lines if they are indented)
            rf"(?:[^\r\n]{{1,{self.MAX_SENTENCE_LENGTH}}}(?:[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}})?(?=\s|$)(?:\r?\n[ \t]+[^\r\n]*)*)" +
            "|" +
            # 10. Quoted text, parentheticals, or bracketed content
            rf"(?<!\w)\"\"\"[^\"]{{0,{self.MAX_QUOTED_TEXT_LENGTH}}}\"\"\"(?!\w)" +
            rf"|(?<!\w)(?:['\"\`])[^\r\n]{{0,{self.MAX_QUOTED_TEXT_LENGTH}}}\g<1>(?!\w)" +
            rf"|\([^\r\n()]{0,{self.MAX_PARENTHETICAL_CONTENT_LENGTH}}(?:\([^\r\n()]{0,{self.MAX_PARENTHETICAL_CONTENT_LENGTH}}\)[^\r\n()]{0,{self.MAX_PARENTHETICAL_CONTENT_LENGTH}}){{0,{self.MAX_NESTED_PARENTHESES}}}\)" +
            rf"|\[[^\r\n\[\]]{{0,{self.MAX_PARENTHETICAL_CONTENT_LENGTH}}}(?:\[[^\r\n\[\]]{{0,{self.MAX_PARENTHETICAL_CONTENT_LENGTH}}}\][^\r\n\[\]]{{0,{self.MAX_PARENTHETICAL_CONTENT_LENGTH}}}){{0,{self.MAX_NESTED_PARENTHESES}}}\]" +
            rf"|\$[^\r\n$]{{0,{self.MAX_MATH_INLINE_LENGTH}}}\$" +
            rf"|`[^\r\n`]{{0,{self.MAX_MATH_INLINE_LENGTH}}}`" +
            "|" +
            # 11. Paragraphs (Treats indented lines as part of the same paragraph)
            rf"(?:(?:^|\r?\n\r?\n)(?:<p>)?(?:(?:[^\r\n]{{1,{self.MAX_PARAGRAPH_LENGTH}}}(?:[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}})?(?=\s|$))|(?:[^\r\n]{{1,{self.MAX_PARAGRAPH_LENGTH}}}(?=[\r\n]|$))|(?:[^\r\n]{{1,{self.MAX_PARAGRAPH_LENGTH}}}(?=[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}}])(?:.{{1,{self.LOOKAHEAD_RANGE}}}(?:[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}}])(?=\s|$))?))(?:</p>)?(?:\r?\n[ \t]+[^\r\n]*)*)" +
            "|" +
            # 12. HTML-like tags and their content
            rf"(?:<[a-zA-Z][^>]{{0,{self.MAX_HTML_TAG_ATTRIBUTES_LENGTH}}}(?:>[\s\S]{{0,{self.MAX_HTML_TAG_CONTENT_LENGTH}}}</[a-zA-Z]+>|\s*/>))" +
            "|" +
            # 13. LaTeX-style math expressions
            rf"(?:(?:\$\$[\s\S]{{0,{self.MAX_MATH_BLOCK_LENGTH}}}?\$\$)|(?:\$[^\$\r\n]{{0,{self.MAX_MATH_INLINE_LENGTH}}}\$))" +
            "|" +
            # 14. Fallback for any remaining content (Keep content together if it's indented)
            rf"(?:(?:[^\r\n]{{1,{self.MAX_STANDALONE_LINE_LENGTH}}}(?:[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}})?(?=\s|$))|(?:[^\r\n]{{1,{self.MAX_STANDALONE_LINE_LENGTH}}}(?=[\r\n]|$))|(?:[^\r\n]{{1,{self.MAX_STANDALONE_LINE_LENGTH}}}(?=[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}}])(?:.{{1,{self.LOOKAHEAD_RANGE}}}(?:[.!?…]|\.\.\.|[\u2026\u2047-\u2049]|\p{{Emoji_Presentation}}\p{{Extended_Pictographic}}])(?=\s|$))(?:\r?\n[ \t]+[^\r\n]*)?))" +
            r")",
            re.MULTILINE | re.UNICODE
        )

    def split(self, text):
        matches = self.chunk_regex.findall(text)
        result, index, offset = [], 0, 0
        for m in matches:
            length = len(m[0])
            result.append({
                'page_content': m[0],
                'metadata': {
                    'index': index,
                    'offset': offset,
                    'length': length,
                    'strip': len(m[0].strip()),
                }
            })
            offset += length
            index += 1
        return result

default_splitter = TextSplitter()

def splitter(text):
    return default_splitter.split(text)

if __name__ == "__main__":
    with open('./README.md', 'r', encoding='utf-8') as f:
        text = f.read()
    for item in default_splitter.split(text):
        print(item)
