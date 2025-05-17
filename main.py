import asyncio
import json
import tika
import logging
import tornado.log
import tornado.ioloop
import tornado.web
from tika import parser
from text_splitter import splitter


class ParserHandler(tornado.web.RequestHandler):

    async def post(self, *args, **kwargs):
        assert 'file' in self.request.files and len(self.request.files) > 0, 'need post file with formdata'
        file_obj = self.request.files['file'][0]
        filename, body = file_obj['filename'], file_obj['body']
        parsed = parser.from_buffer(
            body,
            headers={
                "X-Tika-PDFextractInlineImages": "true",
            },
        )
        assert 'content' in parsed and parsed['content'], 'parsed error'
        result = splitter(parsed['content'])
        for chunk in result:
            chunk['metadata']['filename'] = filename            
        self.finish(json.dumps(result, ensure_ascii=False))


def main():
    tika.initVM()
    tornado.log.enable_pretty_logging()
    app = tornado.web.Application([(r"/", ParserHandler)])
    app.listen(8888)
    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
