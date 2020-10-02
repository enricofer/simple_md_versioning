from functools import reduce
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.forms.models import model_to_dict
from .models import Version
from django.http import JsonResponse, HttpResponse, HttpResponseRedirect
import diff_match_patch as dmp_module
from .diff3 import diff3, merge as merge3, SEPARATORS
from .conflicts import getConflicts
from markdown import markdown
import pypandoc

from django.views.generic import View
from jwt_auth.mixins import JSONWebTokenAuthMixin

from django.core.files.temp import NamedTemporaryFile
from django.core import files

import sys
import re
import json
import os
import subprocess
import difflib
import tempfile
import zipfile
from shutil import copyfile

from io import StringIO, BytesIO

dmp = dmp_module.diff_match_patch()


class RestrictedView(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        versione = Version.objects.get(pk=id)
        details = versionDetails(versione)
        details["username"] = request.user.username
        return JsonResponse(details)

def conta_rami(versione):
    rami = Version.objects.filter(parent__pk=versione.pk)
    print ('rami:',rami, len(rami), file=sys.stderr)
    return len(rami)

def versionDetails(v):
    vdict = model_to_dict(v)
    children = Version.objects.filter(parent__pk=v.pk)
    vdict["hasChildren"] = True if children else False
    if v.parent:
        conflicts_check = getConflicts(v, quick=True)
        if conflicts_check != v.conflicts:
            print ('conflicts_check:', conflicts_check, file=sys.stderr)
            v.conflicts = conflicts_check["conflicts"]
            if conflicts_check["conflicts"] == 0:
                v.status = 'Version'
            else:
                v.status = 'Conflicted'
            v.save()
    return vdict

class new_version_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        return new_version(requests, id)

# Create your views here.
def new_version(request, master_id):
    documento_master = Version.objects.get(pk=master_id)
    versione = Version()
    if versione.parent:
        versione.status = 'master'
    else:
        versione.status = 'version'
    versione.parent = documento_master
    versione.title = "ramo-%s" % str(conta_rami(documento_master) + 1)
    versione.base = documento_master.content
    versione.content = documento_master.content
    print ('patch:',versione, file=sys.stderr)
    versione.save()
    return JsonResponse(versionDetails(versione))

class conflicts_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        return conflicts(requests, id)

@csrf_exempt
def conflicts(requests, id):
    versione = Version.objects.get(pk=id)
    return JsonResponse(getConflicts(versione))

class merge_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        return merge(requests, id)

@csrf_exempt
def merge(request, id ):
    versione = Version.objects.get(pk=id)
    if not versione.parent:
       return JsonResponse({"result":"ko", "error": "This is master version without merge target. Can't merge"}, status=500) 
    if versione.conflicts == 0:
        patch = dmp.patch_fromText(versione.patch)
        res_patch =  dmp.patch_apply(patch, versione.parent.content)
        reconciliable =  reduce(lambda a,b: a and b, res_patch[1], True)
        if reconciliable:
            versione.parent.content = res_patch[0]
            versione.parent.save()
            versione.patch = "RECONCILIATED"
            versione.status = 'History'
            versione.title = versione.title + "__reconciliated"
            versione.save()
            details = versionDetails(versione.parent)
            print("MERGE", details)
            return JsonResponse({"parent_id": versione.parent.pk })

    return JsonResponse({"result":"ko", "error": "the version has conflicts. Can't merge"}, status=500)

'''
def edit(request, id):
    versione = Version.objects.get(pk=id)
    return render(request, 'editor.html', {"version": versione, "content_html": markdown(versione.content)})
'''

def html2pdf (html):
    with open(page_html_ROOT, 'wb') as html_file:
        html_file.write(bytes(html, 'UTF-8'))

    execute ('iconv -f UTF-8 -t ISO-8859-1 -o %s  %s' % (iso_html_ROOT,page_html_ROOT))
    execute ('htmldoc --bodyfont HELVETICA --size A4 --right 26mm --left 26mm --top 20mm --bottom 20mm --fontsize 13 --fontspacing 1.4 --webpage -f %s %s' % ( page_pdf_ROOT, iso_html_ROOT))
    execute ('rm %s  %s' % (iso_html_ROOT, page_html_ROOT))

@csrf_exempt
def docx(request, id):
    return download(request, 'docx', id)
    
@csrf_exempt
def pdf(request, id):
    return download(request, 'pdf', id)

class odt_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        return odt(requests, id)

@csrf_exempt
def odt(request, id):
    return download(request, 'odt', id)

@csrf_exempt
def download(request, format, id):

    def zipdir(path, ziph):
        # ziph is zipfile handle
        for root, dirs, files in os.walk(path):
            for filename in files:
                ziph.write(os.path.join(root,filename),os.path.join(root,filename)[len(dezipDir):])

    basedir = tempfile.mkdtemp()
    md_file_path = os.path.join(basedir, "input.md")
    out_file_path = os.path.join(basedir, "output." + format)
    versione = Version.objects.get(pk=id)
    #FASE1 generazione del file odt del contenuto corrente
    pypandoc.convert_text(versione.content, format, format='md', outputfile=out_file_path)

    if versione.parent and format == 'odt':
        #FASE2_1 copia di backup

        #FASE2 scompattamento del file odt
        dezipDir = os.path.join(basedir, "raw_odt")
        with zipfile.ZipFile(out_file_path, 'r') as zip_ref:
            zip_ref.extractall(dezipDir)
        #FASE3 creazione directory Versions dentro directory zippata
        dezipVersionDir = os.path.join(dezipDir, "Versions")
        print ("dezipVersionDir", dezipVersionDir)
        os.mkdir(dezipVersionDir)
        #FASE4 generazione del file odt del contenuto master
        master_file = os.path.join(dezipVersionDir, "Version1")
        pypandoc.convert_text(versione.parent.content, format, format='md', outputfile=master_file)
        #FASE5 creazione file versionsList.xml
        template = """<?xml version="1.0" encoding="UTF-8"?><VL:version-list xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:VL="http://openoffice.org/2001/versions-list"><VL:version-entry VL:title="Version1" VL:comment="%s" VL:creator="user" dc:date-time="%s"/></VL:version-list>"""
        versionListTxt = template % ("user", versione.parent.modify_date.strftime("%Y-%m-%dT%H:%M:%S")) #2020-09-28T08:58:50
        versionListFilePath = os.path.join(dezipDir, "VersionList.xml")
        with open(versionListFilePath,'w') as versionListFile:
            versionListFile.write(versionListTxt)
        #FASE6 MODIFICA META-INF/manifest.xml
        manifestFilePath = os.path.join(dezipDir, "META-INF", "manifest.xml")
        with open(manifestFilePath,'r') as manifestFile:
            manifestFileContent = manifestFile.read()
        versionsMetafileEdit = """   <manifest:file-entry manifest:full-path="VersionList.xml" manifest:media-type=""/>\n"""
        versionsMetafileEdit += """   <manifest:file-entry manifest:full-path="Versions/Version1" manifest:media-type=""/>\n"""
        manifestFileNewContent = manifestFileContent[:-20]+versionsMetafileEdit+manifestFileContent[-20:]
        os.remove(manifestFilePath)
        with open(manifestFilePath,'w') as manifestFile:
            manifestFile.write(manifestFileNewContent)
        #FASE8 modifica stili
        os.remove(os.path.join(dezipDir,"styles.xml"))
        copyfile(os.path.join(os.path.dirname(os.path.realpath(__file__)),"styles.xml"), os.path.join(dezipDir,"styles.xml"))
        #FASE8 rimozione odt versione corrente
        os.remove(out_file_path)
        #FASE9 creazione nuovo odt da compressione directory precedenti
        out_file = zipfile.ZipFile(out_file_path, 'w', zipfile.ZIP_STORED)
        zipdir(dezipDir, out_file)
        out_file.close()

    stream = open(out_file_path, "rb")
    response = HttpResponse(stream, content_type="application/vnd.openxmlformats") #application/pdf
    response['Content-Disposition'] = 'attachment; filename=%s.%s' % (versione.title,format)
    return response

class upload_restricted(JSONWebTokenAuthMixin, View):
    def post(self, request, id):
        return upload(request, id)

@csrf_exempt
def upload(request, id):
    if request.method == 'POST':
        if id:
            versione = Version.objects.get(pk=id)
        else:
            versione = Version()
        upload = request.FILES['uploaded_content']
        print ("MIMETYPE", upload.content_type)
        if upload.content_type in ("text/markdown", "text/plain"):
            versione.content = upload.read()
        elif upload.content_type in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ):
            basedir = tempfile.mkdtemp()
            md_file = os.path.join(basedir, "input.md")
            in_file = os.path.join(basedir, "output.docx")
            with open(in_file, 'wb') as dest:
                dest.write(upload.read())
            output = pypandoc.convert(in_file, "md", format='docx', outputfile=md_file)
            with open(md_file,"r") as md:
                versione.content = md.read()
        versione.save()
        return JsonResponse({"result": "OK", "version_id":versione.pk})

class details_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        return details(requests, id)

@csrf_exempt
def details(request, id):
    versione = Version.objects.get(pk=id)
    return JsonResponse(versionDetails(versione))

class delete_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        return delete(requests, id)

@csrf_exempt
def delete(request, id):
    versione = Version.objects.get(pk=id)
    deleted = model_to_dict(versione)
    versione.delete()
    return JsonResponse({"deleted": "ok", "deleted_version": deleted })

class vlist_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, id):
        return vlist(requests, id)

@csrf_exempt
def vlist(request, fromId):
    return vtree(request, fromId, asList = True)

def getVersionObject(v):
    return {
        "title": v.title,
        "id": v.pk,
        "path": str(v),
        "parent_name": v.parent.title if v.parent else "",
        "parent_id": v.parent.pk if v.parent else -1,
        "conflicted": v.patch == "CONFLICTED",
        "reconciliated": v.patch == "RECONCILIATED",
        "master": False if v.parent else True
    }    

class vtree_restricted(JSONWebTokenAuthMixin, View):
    def get(self, request, fromId, asList = False):
        return vtree(request, fromId, asList)

@csrf_exempt
def vtree(request, fromId, asList = False):
    tree = []

    def traverse_nodes(node):
        node_content = getVersionObject(node)
        children = Version.objects.filter(parent__pk=node.pk)
        node_content["hasChildren"] = True if children else False
        node_content["text"] = node_content["title"]
        node_content["draggable"] = False
        node_content["droppable"] = False
        node_content["children"] = []

        for child in children:
            if asList:
                tree.append(traverse_nodes(child))
            else:
                node_content["children"].append(traverse_nodes(child))
        return node_content
    if fromId:
        root_nodes = Version.objects.filter(pk=fromId)
    else:
        root_nodes = Version.objects.filter(parent__pk=None)
    print ('root_nodes:',root_nodes, file=sys.stderr)
    for node in root_nodes:
        tree.append(traverse_nodes(node))

    return JsonResponse({"versions":tree})

class save_restricted(JSONWebTokenAuthMixin, View):
    def post(self, request):
        return save(request)

@csrf_exempt
def save(request):
    if request.method == 'POST':
        body = request.body.decode('utf-8')
        postData = json.loads(body)
        print ('postData:\n',postData["pk"], file=sys.stderr)
        if postData["pk"] > 0:
            versione = Version.objects.get(pk=postData["pk"])
        else:
            versione = Version()
        if versione.patch == "RECONCILIATED":
            return JsonResponse({"result":"ko", "version_id": versione.pk, "error": "Reconciliated Versions cannot be modified"}, status=500)
        versione.content = postData['content']
        print ('NEW_CONTENT:\n',postData['content'], file=sys.stderr)
        versione.title = postData['title']
        versione.save()
        return JsonResponse({"result":"ok", "version_id": versione.pk, "error": ""})
    else:
        return JsonResponse({"result":"ko", "error": "wrong http method"}, status=500)


