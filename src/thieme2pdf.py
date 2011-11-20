#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys, os
import httplib, urllib
import subprocess, shlex
from getopt import getopt, GetoptError
from tempfile import NamedTemporaryFile
import binascii, codecs
from xml.etree.ElementTree import fromstring
import logging
import shutil

logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)

def getCookie():
    """ get valid http cookie """
    # curl -I ebooks.thieme.de/viewer/viewer.php
    conn = httplib.HTTPConnection("ebooks.thieme.de:80")
    conn.request("GET","/viewer/viewer.php")
    res = conn.getresponse()
    cookie = False
    for item in res.getheaders():
        if item[0] == "set-cookie":
            cookie = item[1]
    if not cookie:
        sys.exit("could not get valid cookie!")
    cookie = cookie.split("ojid=")[1]
    cookie = cookie.split(";")[0]
    
    # now we need to login to make the cookie valid
    headers = {"Cookie": "ojid=%s" % cookie}
    conn.request("GET","/login.php", headers=headers)
    res = conn.getresponse()
    return cookie

def getRawToc(isbn):
    cookie = getCookie()
    isbn_hex = binascii.hexlify(str(isbn))
    params = binascii.unhexlify('00030000000100046e756c6c00022f37000001060a\
00000001110a81134f666c65782e6d6573736167696e672e6d657373616765732e52656d6f7\
4696e674d6573736167650d736f75726365136f7065726174696f6e0f686561646572730962\
6f647911636c69656e7449641574696d65546f4c6976651764657374696e6174696f6e13746\
96d657374616d70136d6573736167654964061164622e67657444420619676574426f6f6b6d\
61726b320a0b01094453496406076e696c154453456e64706f696e7406136d792d616d66706\
87001090301061f28' + isbn_hex + '29010400060d616d66706870040006494631453635\
3738422d353130432d334134352d3345\
41302d444633314437413944453445')
    headers = {'Content-Type': 'application/x-amf', "Cookie": "ojid=%s" % cookie}
    conn = httplib.HTTPConnection("ebooks.thieme.de:80")
    conn.request("POST","/viewer/amfphp/gateway.php", params, headers)
    response = conn.getresponse()
    rv = response.read()
    return rv

def getToc(isbn):
    rawToc = getRawToc(isbn)
    rawToc = "<root" + rawToc.split("<root", 1)[1]
    rawToc = rawToc.rsplit("</root>", 1)[0] + "</root>"
    tree = fromstring( rawToc.decode("latin_1", "replace").encode('utf-8') )
    return tree

def formatOutput(subtree, ident, pageOffset, out):
    for item in subtree:
        page = int(item.attrib["page"])
        label = item.attrib["label"]
        if page-pageOffset > 1:
            out += "\n" + ident*"\t" + "%s/%i" % (label, page-pageOffset)
        if len(item) > 1:
            out = formatOutput(item, ident+1, pageOffset, out)
    return out

def create_jpdfbookmarks(tree, pageOffset):
    return formatOutput(tree[0], 0, pageOffset , "").strip()

def downloadChunk(isbn=None, start=0, stop=0, cookie=False):
    """ download the given range of pages (start/stop) """
    params = urllib.urlencode({'isbn': isbn, 'page': start, 'endPage': stop})
    headers = {'Content-Type': 'application/x-www-form-urlencoded', "Cookie": "ojid=%s" % cookie}
    conn = httplib.HTTPConnection("ebooks.thieme.de:80")
    conn.request("POST","/download.php", params, headers)
    response = conn.getresponse()
    rv = response.read()
    # crappy webserver does not return correct status codes
    if "<b>Fatal error</b>:" in rv:
        return False
    else:
        return rv
    
def downloadBook(isbn, dest, stepsize=10, pageOffset=0, generateTOC=True):
    """ Download the ebook with the given isbn to dest """
    #tmpfile = NamedTemporaryFile(suffix='.pdf')
    cookie = getCookie()
    currentPage = pageOffset + 1
    pages = []
    
    while stepsize >= 0:
        logging.info("downloading pages %i - %i" % (currentPage, currentPage+stepsize) )
        chunk = downloadChunk(isbn=isbn, start=currentPage, stop=currentPage+stepsize, cookie=cookie)
        if chunk:
            page = {}
            pages.append(page)
            page["rawpdf"] = NamedTemporaryFile(suffix='.pdf', mode='wb')
            #page["rawpdf"].write(chunk)  
            open(page["rawpdf"].name, 'wb').write(chunk)
            logging.debug("rawpdf file: %s" % page["rawpdf"].name)
            currentPage += stepsize + 1
        else:
            logging.info("did not succeed. reducing stepsize")
            stepsize -= 1
    
    logging.info("convert pdf to ps")
    for page in pages:
        logging.info("processing page set %i of %i" % (pages.index(page)+1, len(pages)) )
        page["rawps"] = NamedTemporaryFile(suffix='.ps', mode='wb')
        command = extBin["pdftops"] + " -paper match %s %s" % (page["rawpdf"].name, page["rawps"].name)
        logging.debug("command: %s" % command)
        args = shlex.split(command)
        subprocess.call(args)
    
    logging.info("and now back to pdf again")
    for page in pages:
        logging.info("processing page set %i of %i" % (pages.index(page)+1, len(pages)) )
        page["cleanpdf"] = NamedTemporaryFile(suffix='.pdf', mode='wb')
        command = extBin["ps2pdf"] + " %s %s" % (page["rawps"].name, page["cleanpdf"].name)
        logging.debug("command: %s" % command)
        args = shlex.split(command)
        subprocess.call(args)
    
    logging.info("merging")
    command = extBin["pdftk"] + " "
    for page in pages:
        command += "%s " % page["cleanpdf"].name
    mergedpdf = NamedTemporaryFile(suffix='.pdf', mode='wb')
    command += " cat output %s" % mergedpdf.name
    logging.debug("command: %s" % command)
    logging.debug("merged pdf: %s" % mergedpdf.name)
    args = shlex.split(command)
    subprocess.call(args)
    
    pdfwithtoc = NamedTemporaryFile(suffix='.pdf', mode='wb')
    if generateTOC:
        logging.info("generating TOC")
        toc = create_jpdfbookmarks( getToc(isbn), pageOffset )
        tocfile = NamedTemporaryFile(suffix='.txt', mode='w')
        f = open(tocfile.name, 'wb')
        f.write(codecs.BOM_UTF8)
        f.write(toc.encode("utf-8"))
        f.close()
        logging.debug("----- TOC -----\n%s\n---------------" % toc)
        command = extBin["jpdfbookmarks"] + " --force --encoding UTF-8 --apply %s --out %s %s" %  (tocfile.name, pdfwithtoc.name, mergedpdf.name)
        logging.debug("command: %s" % command)
        args = shlex.split(command)
        subprocess.call(args)
    
    if os.path.getsize(pdfwithtoc.name) > 0:
        logging.info("successfully written TOC")
        shutil.copy(pdfwithtoc.name, dest)
    else:
        logging.info("no TOC was written!")
        shutil.copy(mergedpdf.name, dest)
    logging.info("written pdf to %s" % dest)
    


extBin = dict(pdftops=None,ps2pdf=None,pdftk=None,jpdfbookmarks=None)
for key in extBin.keys():
    try:
        if key == "jpdfbookmarks":
            extBin[key] = 'java -jar jpdfbookmarks.jar'
        else:
            subprocess.Popen([key], stderr=subprocess.STDOUT, stdout=subprocess.PIPE).communicate()
            extBin[key] = key
    except:
        sys.exit("could not find %s exiting!" % key)


usage = """Syntax: thieme2pdf --isbn=ISBN --out=name_of_the_book.pdf

  -h, --help               print this help text
  -v                       enable verbose logging
  --isbn=                  specify the isbn of the book
  --out=                   specify the output file
  --offset=                set a page offset from where to begin download
                           default is 1 (first page is not downloadable)
"""

if __name__ == "__main__":
    
    #assemble isbn and page offset
    stepsize = 10
    isbn = None
    pageOffset = 1
    dest = None
    args = sys.argv[1:]
    try:
        opts, args = getopt(args, 'vh', ['isbn=', 'out=', 'offset=', 'help'])
    except GetoptError:
        sys.exit("could not parse command line arguments.")
    for opt, value in opts:
        if opt == "--isbn":
            try:
                isbn = int(value)
            except:
                sys.exit("enter isbn as unseparated number sequence. i.e.: 9783131395818")
        elif opt == "--offset":
            try:
                pageOffset = int(value)
            except:
                sys.exit("offset must be a numerical value!")
        elif opt == "-v":
            logger = logging.getLogger()
            logger.setLevel(logging.DEBUG)
            logging.debug("verbose logging enabled")
        elif opt == "-h" or opt == "--help":
            sys.exit(usage)
        elif opt == "--out":
            dest = value
    if not isbn:
        while not isbn:
            try:
                isbn = int(raw_input('ISBN: '))
            except:
                logging.error("enter isbn as unseparated number sequence. i.e.: 9783131395818")
    
    if not dest:
        dest = "%i.pdf"%isbn
    
    try:
        logging.info("ISBN: %i offset: %i out: %s"%(isbn, pageOffset, dest))
        downloadBook(isbn, dest, pageOffset=pageOffset)
    except KeyboardInterrupt:
        sys.exit("Keyboard Interrupt")
        