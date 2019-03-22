#!/bin/env python3
"""
Copyright 2018-2019 Trustees of Indiana University

This code is licensed under the APACHE 2.0 License

Original code by Brian Wheeler (bdwheele@indiana.edu)

-------

This will process a file and create either a data structure (if called as a library) or 
a JSON blob (when called from the command line) of information about a given file.

This effectively combines ffprobe, imagemagic identify, file, and pdfinfo.  Other formats
can easily be added.   If the external tools are not available, only basic information will
be created.

"""

import argparse
import json
import os
import subprocess
import fractions
import re
import zipfile


# Default locations of these tools on most linux boxes
default_programs = {
    'ffprobe': "/usr/bin/ffprobe",
    'identify': "/usr/bin/identify",
    'file': "/usr/bin/file",
    'pdfinfo': "/usr/bin/pdfinfo"
}


def probe_file(file, programs=default_programs):
    """
    Run all of the probes on a given file.
    """
    data = {'container': {'name': os.path.basename(file),
                          'size': os.path.getsize(file),
                          'time': os.path.getmtime(file)
                      },
            'streams': {},
            'tags': {}
            }
    if os.path.isfile(file):
        data['container']['type'] = 'file'
    elif os.path.isdir(file):
        data['container']['type'] = 'dir'
    else:
        data['container']['type'] = 'other'

    mime = get_mime_type(file, programs)
    data['container']['mime_type'] = mime

    for probe in (probe_time_based_media, probe_image_media, probe_text, probe_document):
        probe(file, data, programs)
    return data


def is_valid_program(program):
    """
    Test to make sure a program is present and executable
    """
    return (program is not None and 
            os.path.exists(program) and 
            os.path.isfile(program) and 
            os.access(program, os.X_OK))
    


def get_mime_type(file, programs):
    """
    Use the file command to get the mime type for a file
    """    
    if is_valid_program(programs['file']):
        result = subprocess.run([programs['file'], '--brief', '--mime-type', '--dereference', file], capture_output=True, check=True)
        mime = str(result.stdout, 'utf-8').rstrip()
    else:
        mime = "unknown/unknown"
    return mime

def probe_time_based_media(file, data, programs):
    """
    Use ffprobe to get information about a file and update the data
    structure with that information.
    """
    if not re.match(r"(audio|video)/", data['container']['mime_type']):
        return

    if not is_valid_program(programs['ffprobe']):
        return 

    result = subprocess.run([programs['ffprobe'], '-v', '0', '-print_format', 'json', '-show_format', '-show_streams', file],
                            check=True, capture_output=True)
    f = json.loads(str(result.stdout, 'utf-8'))
    
    data['container']['duration'] = float(f['format']['duration'])
    data['container']['format'] = f['format']['format_name']
    if 'tags' in f['format']:
        data['container']['tags'] = {}
        for t in f['format']['tags']:
            data['container']['tags'][t] = f['format']['tags'][t]

    counter = 0
    data['streams'] = {}
    for stream in f['streams']:
        s = {}
        if stream['codec_type'] == 'audio':
            s['@type'] = 'audio'
            if 'codec_name' in  stream:
                s['codec'] = stream['codec_name']
            elif 'codec_tag_string' in stream:
                s['codec'] = stream['codec_tag_string']
            if 'duration' in stream:
                s['duration'] = float(stream['duration'])
            elif 'tags' in stream and 'DURATION' in stream['tags']:
                # matroska stores the duration in the tags in hh:mm:ss.sss format
                s['duration'] = hhmmss2secs(stream['tags']['DURATION'])
            else:
                s['duration'] = data['container']['duration']
            s['sample_format'] = stream['sample_fmt']
            s['channels'] = stream['channels']
            if 'channel_layout' in stream:
                s['channel_layout'] = stream['channel_layout']
            else:
                # make some educated guesses
                if s['channels'] == 1:
                    s['channel_layout'] = 'mono'
                elif s['channels'] == 2:
                    s['channel_layout'] = 'stereo'
            s['sample_rate'] = int(stream['sample_rate'])
            if 'bits_per_sample' in stream:
                s['bits_per_sample'] = int(stream['bits_per_sample'])
            elif 'bits_per_raw_sample' in stream:
                s['bits_per_sample'] = int(stream['bits_per_raw_sample'])
            if s['bits_per_sample'] == 0:
                s.pop('bits_per_sample')

            if 'bit_rate' in stream:
                s['bit_rate'] = int(stream['bit_rate'])
            if 'tags' in stream:
                s['user_data'] = {}
                for t in stream['tags']:
                    s['user_data'][t] = stream['tags'][t]


        elif stream['codec_type'] == 'video':
            s['@type'] = 'video'
            if 'codec_name' in  stream:
                s['codec'] = stream['codec_name']
            elif 'codec_tag_string' in stream:
                s['codec'] = stream['codec_tag_string']

            # duration
            if 'duration' in stream:
                s['duration'] = float(stream['duration'])
            elif 'tags' in stream and 'DURATION' in stream['tags']:
                # matroska stores the duration in the tags in hh:mm:ss.sss format
                s['duration'] = hhmmss2secs(stream['tags']['DURATION'])
            else:
                s['duration'] = data['container']['duration']
            
            # get video dimensions
            dims = {'width': stream['width'],
                    'height': stream['height']}
            if stream['width'] > 0 and stream['height'] > 0:
                if 'sample_aspect_ratio' not in stream:
                    # without any information, we'll assume 1:1
                    stream['sample_aspect_ratio'] = '1:1'
                dims['sample_aspect_ratio'] = ratio2fraction(stream['sample_aspect_ratio'])
                
                if 'display_aspect_ratio' not in stream:
                    # without any information, the ratio is the sample aspect ratio and
                    # the ratio of width and height...using some math.
                    sar = fractions.Fraction(dims['sample_aspect_ratio'][0],
                                            dims['sample_aspect_ratio'][1])
                    par = fractions.Fraction(stream['width'], stream['height'])
                    stream['display_aspect_ratio'] = sar * par
                dims['display_aspect_ratio'] = ratio2fraction(stream['display_aspect_ratio'])
            s['dimensions'] = dims
            if 'pix_fmt' in stream:
                s['pixel_format'] = stream['pix_fmt']
            if 'bit_rate' in stream:
                s['bit_rate'] = int(stream['bit_rate'])
            if 'bits_per_sample' in stream:
                s['bits_per_sample'] = int(stream['bits_per_sample'])
            elif 'bits_per_raw_sample' in stream:
                s['bits_per_sample'] = int(stream['bits_per_raw_sample'])

            s['frame_rate'] = float(fractions.Fraction(stream['r_frame_rate']))
            if 'color_space' in stream:
                s['color_space'] = stream['color_space']
            if 'color_transfer' in stream:
                s['color_transfer'] = stream['color_transfer']
            if 'profile' in stream:
                s['codec_profile'] = stream['profile']
            if 'tags' in stream:
                s['user_data'] = {}
                for t in stream['tags']:
                    s['user_data'][t] = stream['tags'][t]
        else:
            # for a data stream, we're just going to dump the tags...we don't
            # it could be a timecode or subtitle stream, or something else.
            s['@type'] = 'data'
            if 'tags' in stream:
                s['user_data'] = {}
                for t in stream['tags']:
                    s['user_data'][t] = stream['tags'][t]
        
        
        if s['@type'] not in data['streams']:
            data['streams'][s['@type']] = []
        s['@position'] = counter

        data['streams'][s['@type']].append(s)
        counter += 1 


def hhmmss2secs(duration):
    "Convert a hh:mm:ss.sss string to seconds."
    parts = duration.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

def ratio2fraction(ratio):
    if type(ratio) is str:
        if ':' in ratio:
            ratio = ratio.replace(':', "/")
    f = fractions.Fraction(ratio)
    return [f.numerator, f.denominator]


def probe_image_media(file, data, programs):
    """
    Use ImageMagick's identify to gather information about a file
    """
    if not re.match(r"image/", data['container']['mime_type']):
        return
    if not is_valid_program(programs['identify']):
        return

    format_string = '''{
  "@type": "image",
  "dimensions": {
    "width": %[width],
    "height": %[height],
    "resolution": {
      "horizontal": %[resolution.x],
      "vertical": %[resolution.y],
      "unit": "%[units]"
    }
  },
  "pixel_type": "%[channels]",
  "codec": "%[magick]",
  "bit_depth": %[bit-depth],
  "compression": "%[compression]",
  "color_profile": "%[profile:icc]"
},'''
    result = subprocess.run([programs['identify'], '-format', format_string, file], 
                            capture_output=True, check=True)
    data['streams'] = {'image': []}
    counter = 0
    for image in json.loads('[' + str(result.stdout, 'utf-8').rstrip().rstrip(',') + "]"):
        i = image
        r = i['dimensions']['resolution']
        if r['unit'].startswith("PixelsPer"):
            r['unit'] = r['unit'][9:].lower()
        else:
            r.pop('unit')
        if r['horizontal'] == 0 or r['vertical'] == 0:
            i['dimensions'].pop('resolution')
        if i['color_profile'] == "":
            i.pop('color_profile')
        
        for k in ('codec', 'compression', 'pixel_type'):
            i[k] = i[k].lower()
        i['@position'] = counter
        data['streams']['image'].append(i)
        counter += 1
 

def probe_text(file, data, programs):
    """ 
    Probe a text file for more information.
    """
    
    if (not re.match(r"text/", data['container']['mime_type'])
        and not re.match(r"application/xml", data['container']['mime_type'])):
        return
    s = {'@type': 'text'}

    if not is_valid_program(programs['file']):
        return

    result = subprocess.run([programs['file'], '--brief', '--mime-encoding', '--dereference', file], capture_output=True, check=True)
    s['encoding'] = str(result.stdout, 'utf-8').rstrip()

    result = subprocess.run([programs['file'], '--brief', '--dereference', file], capture_output=True, check=True)
    s['description'] = str(result.stdout, 'utf-8').rstrip()
    
    # If this is XML, then let's do a little more probing...basically get the root
    # node, and perhaps a better encoding.  Don't actually parse the XML, just use
    # some regexes
    if data['container']['mime_type'].endswith("/xml"):
        with open(file, "r") as f:
            content = f.read()
            m = re.search(r"<\?xml[^>]+encoding=[\"\'](.+?)[\"\']", content)
            if m:
                s['encoding'] = m.group(1)
            m = re.search(r"<([a-zA-Z_].+?)[\s>]", content)
            if m:
                s['description'] = f"XML Document with '{m.group(1)}' root node"
    
    data['streams'] = {'text': []}
    s['@position'] = 0
    data['streams']['text'].append(s)


def probe_document(file, data, programs):
    """
    Handle office-type documents:  pdf, doc, docx, xls, xlsx, ppt, pptx
    """
    s = {'@type': 'document'}
    mime_type = data['container']['mime_type']
    if mime_type == "application/pdf":
        if not is_valid_program(programs['pdfinfo']):
            return

        result = subprocess.run([programs['pdfinfo'], file], capture_output=True, check=True)
        for l in str(result.stdout, 'utf-8').rstrip().split("\n"):
            print(l)
            k, v = [x.strip() for x in l.split(":", 1)]
            print(f"K={k}, V={v}")
            if k == "PDF version":
                s['version'] = v
            elif k == "Pages":
                s['pages'] = int(v)
            elif k == "Page size":
                s['page_size'] = v
            elif k in ('Creator', 'Producer', 'Tagged'):
                if v != "":
                    if 'user_data' not in s:
                        s['user_data'] = {}
                    s['user_data'][k] = v

    elif mime_type.startswith("application/vnd.openxmlformats-officedocument."):
        # docx, pptx, xlsx
        if zipfile.is_zipfile(file):
            with zipfile.ZipFile(file, mode="r") as z:
                files = z.namelist()
                # let's pull some useful information from the core metadata
                core = str(z.read("docProps/core.xml"), "utf-8")
                for t in ["dc:creator", "dc:description", "dc:language", "cp:lastModifiedBy", "cp:revision",
                          "dc:subject", "dc:title", "dcterms:modified", "dcterms:created"]:
                    m = re.search(f"<{t}\\b.*?>(.+?)</{t}>", core)
                    if m:
                        k = t.split(":")[1]
                        v = m.group(1).strip()
                        if v != "":
                            if 'user_data' not in s:
                                s['user_data'] = {}
                            s['user_data'][k] = v

                if "word/document.xml" in files:
                    # this is a word document
                    s['description'] = "Word 2007+ Document"                
                    if "docProps/app.xml" in files:
                        props = str(z.read("docProps/app.xml"), "utf-8")
                        # find pages for word documents
                        m = re.search(r"<Pages>(\d+)</Pages>", props)
                        if m:
                            s['pages'] = int(m.group(1))
                elif "xl/workbook.xml" in files:
                    s['description'] = "Excel 2007+ Document"
                    sheets = [x for x in files if re.match(r"xl/worksheets/sheet\d+\.xml", x)]
                    s['pages'] = len(sheets)

                elif "ppt/presentation.xml" in files:
                    s['description'] = "Powerpoint 2007+ Document"
                    slides = [x for x in files if re.match(r"ppt/slides/slide\d+\.xml", x)]
                    s['pages'] = len(slides)
    elif mime_type.startswith("application/vnd.oasis.opendocument."):
        # odp, ods, odt
        if zipfile.is_zipfile(file):
            with zipfile.ZipFile(file, mode="r") as z:
                files = z.namelist()
                # let's pull some useful information from the core metadata
                core = str(z.read("meta.xml"), "utf-8")
                for t in ["meta:initial-creator", "meta:creation-date", "dc:date"]:
                    m = re.search(f"<{t}\\b.*?>(.+?)</{t}>", core)
                    if m:
                        k = t.split(":")[1]
                        v = m.group(1).strip()
                        if v != "":
                            if 'user_data' not in s:
                                s['user_data'] = {}
                            s['user_data'][k] = v

                if mime_type.endswith("text"):
                    m = re.search(r"page-count=\"(\d+)\"", core)
                    if m:
                        s['pages'] = m.group(1)
    elif mime_type == "application/msword":
        # doc
        pass
    elif mime_type == "application/vnd.ms-powerpoint":
        # ppt
        pass
    elif mime_type == "application/vnd.ms-excel":
        # xls
        pass
    else:
        # not something we handle.  Just return
        return
    data['streams'] = {'document': []}
    data['streams']['document'].append(s)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Probe a media file for metadata")
    for p in default_programs:
        parser.add_argument(f"--{p}-bin", type=str, default=default_programs[p],
                            help=f"Location of {p} binary ({default_programs[p]})")
    parser.add_argument('mediafile', type=str, metavar="<mediafile>",
                        help="File to probe")
    args = parser.parse_args()
    for p in default_programs:
        default_programs[p] = args.__getattribute__(f"{p}_bin")
    data = probe_file(args.mediafile, default_programs)
    print(json.dumps(data, sort_keys=True, indent=4))

                