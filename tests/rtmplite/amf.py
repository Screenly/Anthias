# Copyright (c) 2011, Kundan Singh. All rights reserved. see README for details.
# 
# Implementation of 
# http://opensource.adobe.com/wiki/download/attachments/1114283/amf0_spec_121207.pdf
# http://opensource.adobe.com/wiki/download/attachments/1114283/amf3_spec_121207.pdf

import struct, datetime, time, types
from StringIO import StringIO
import xml.etree.ElementTree as ET

class Object(object): # a typed object or received object. Typed object has _classname attr.
    def __init__(self, **kwargs):
        for key, val in kwargs.items(): setattr(self, key, val)

class Class:
    __slots__ = ('name', 'encoding', 'attrs')

class _Undefined(object):
    def __nonzero__(self): return False # always treated as False
    def __repr__(self): return 'amf.undefined'

undefined = _Undefined()  # received undefined is different from null (None)


class BytesIO(StringIO): # raise EOFError if needed, allow read with optional length, and peek next byte
    def __init__(self, *args, **kwargs): StringIO.__init__(self, *args, **kwargs)
    def eof(self): return self.tell() >= self.len  # return true if next read will cause EOFError
    def remaining(self): return self.len - self.tell() # return number of remaining bytes
    
    def read(self, length=-1):
        if length > 0 and self.eof(): raise EOFError # raise error if reading beyond EOF
        if length > 0 and self.tell() + length > self.len: length = self.len - self.tell() # don't read more than available bytes
        return StringIO.read(self, length)
    def peek(self):
        if self.eof(): return None
        else:
            c = self.read(1)
            self.seek(self.tell()-1)
            return c
        
    for type, T, bytes in (('u8', 'B', 1), ('s8', 'b', 1), ('u16', 'H', 2), ('s16', 'h', 2), ('u32', 'L', 4), ('s32', 'l', 4), ('double', 'd', 8)):
        exec '''def read_%s(self): return struct.unpack("!%s", self.read(%d))[0]'''%(type, T, bytes)
        exec '''def write_%s(self, c): self.write(struct.pack("!%s", c))'''%(type, T)
        
    def read_utf8(self, length): return unicode(self.read(length), 'utf8')
    def write_utf8(self, c): self.write(c.encode('utf8'))
    
    def read_u29(self):
        n = result = 0; b = self.read_u8()
        while b & 0x80 and n < 3: result <<= 7; result |= b & 0x7f; b = self.read_u8(); n += 1
        if n < 3: result <<= 7; result |= b
        else: result <<= 8; result |= b
        assert result & 0xe0000000 == 0
        return result
    def read_s29(self):
        result = self.read_u29()
        if result & 0x10000000: result -= 0x20000000
        return result
    def write_u29(self, c):
        if c < 0 or c > 0x1fffffff: raise ValueError('uint29 out of range')
        bytes = ''
        if c >= 0x200000: bytes += chr(0x80 | ((c >> 22) & 0x7f))
        if c >= 0x4000: bytes += chr(0x80 | ((c >> 15) & 0x7f))
        if c >= 0x80: bytes += chr(0x80 | ((c >> 8) & 0x7f))
        if c >= 0x200000: bytes += chr(c & 0xff)
        else: bytes += chr(c & 0x7f)
        self.write(bytes)
    def write_s29(self, c):
        if c < -0x10000000 or c > 0x0fffffff: raise ValueError('sint29 out of range')
        if c < 0: c += 0x20000000
        self.write_u29(c)


class AMF0(object):
    NUMBER, BOOL, STRING, OBJECT, MOVIECLIP, NULL, UNDEFINED, REFERENCE, ECMA_ARRAY, OBJECT_END, ARRAY, DATE, LONG_STRING, UNSUPPORTED, RECORDSET, XML, TYPED_OBJECT, TYPE_AMF3 = range(0x12)

    def __init__(self, data=None):
        self._obj_refs, self.data = list(), data if isinstance(data, BytesIO) else BytesIO(data) if data is not None else BytesIO()
    def _created(self, obj): # new object-reference is created
        self._obj_refs.append(obj); return obj
    def read(self):
        global undefined
        marker = self.data.read_u8()
        if   marker == AMF0.NUMBER:   return self.data.read_double()
        elif marker == AMF0.BOOL:     return bool(self.data.read_u8())
        elif marker == AMF0.STRING:   return self.readString()
        elif marker == AMF0.OBJECT:   return self.readObject()
        elif marker == AMF0.MOVIECLIP:raise NotImplementedError()
        elif marker == AMF0.NULL:     return None
        elif marker == AMF0.UNDEFINED:return undefined
        elif marker == AMF0.REFERENCE:return self.readReference()
        elif marker == AMF0.ECMA_ARRAY:return self.readEcmaArray()
        elif marker == AMF0.ARRAY:    return self.readArray() 
        elif marker == AMF0.DATE:     return self.readDate()
        elif marker == AMF0.LONG_STRING:return self.readLongString()
        elif marker == AMF0.UNSUPPORTED:return None
        elif marker == AMF0.RECORDSET:raise NotImplementedError()
        elif marker == AMF0.XML:      return self.readXML()
        elif marker == AMF0.TYPED_OBJECT: return self.readTypedObject()
        elif marker == AMF0.TYPE_AMF3: return AMF3(self.data).read()
        else: raise ValueError('Invalid AMF0 marker 0x%02x at %d' % (marker, self.data.tell()-1))
        
    def write(self, data):
        global undefined
        if   data is None:                         self.data.write_u8(AMF0.NULL)
        elif data == undefined:                    self.data.write_u8(AMF0.UNDEFINED)
        elif isinstance(data, bool):               self.data.write_u8(AMF0.BOOL); self.data.write_u8(1 if data else 0)
        elif isinstance(data, (int, long, float)): self.data.write_u8(AMF0.NUMBER); self.data.write_double(float(data))
        elif isinstance(data, types.StringTypes):  self.writeString(data)
        elif isinstance(data, (types.ListType, types.TupleType)): self.writeArray(data)
        elif isinstance(data, (datetime.date, datetime.datetime)): self.writeDate(data)
        elif isinstance(data, ET._ElementInterface): self.writeXML(data)
        elif isinstance(data, types.DictType):     self.writeEcmaArray(data)
        elif isinstance(data, Object) and hasattr(data, '_classname'): self.writeTypedObject(data)
        elif isinstance(data, (Object, object)):   self.writeObject(data)
        else: raise ValueError('Invalid AMF0 data %r type %r' % (data, type(data)))

    def readString(self): return self.data.read_utf8(self.data.read_u16())
    def readLongString(self): return self.data.read_utf8(self.data.read_u32())
    def writeString(self, data, writeType=True):
        data = unicode(data).encode('utf8') if isinstance(data, unicode) else data
        if writeType: self.data.write_u8(AMF0.LONG_STRING if len(data) > 0xffff else AMF0.STRING)
        if len(data) > 0xffff: self.data.write_u32(len(data))
        else: self.data.write_u16(len(data))
        self.data.write(data)
        
    def readObject(self):
        obj, key = self._created(Object()), self.readString()
        while key != '' or self.data.peek() != chr(AMF0.OBJECT_END):
            setattr(obj, key, self.read()); key = self.readString()
        self.data.read(1) # discard OBJECT_END
        return obj
    def writeObject(self, data):
        if not self.writePossibleReference(data):
            self.data.write_u8(AMF0.OBJECT)
            for key, val in data.__dict__.items(): 
                if not key.startswith('_'): self.writeString(key, False); self.write(val)
            self.writeString('', False); self.data.write_u8(AMF0.OBJECT_END)

    def readReference(self): 
        try: return self._obj_refs[self.data.read_u16()]
        except IndexError: raise ValueError('invalid reference index')
    def writePossibleReference(self, data):
        if data in self._obj_refs: self.data.write_u8(AMF0.REFERENCE); self.data.write_u16(self._obj_refs.index(data)); return True
        elif len(self._obj_refs) < 0xfffe: self._obj_refs.append(data)
    
    def readEcmaArray(self):
        len_ignored = self.data.read_u32()
        obj, key = self._created(dict()), self.readString()
        while key != '' or self.data.peek() != chr(AMF0.OBJECT_END):
            obj[int(key) if key.isdigit() else key] = self.read(); key = self.readString()
        self.data.read(1) # discard OBJECT_END
        return obj
    def writeEcmaArray(self, data):
        if not self.writePossibleReference(data):
            self.data.write_u8(AMF0.ECMA_ARRAY); self.data.write_u32(len(data))
            for key, val in data.items(): self.writeString(key, writeType=False); self.write(val)
            self.writeString('', writeType=False); self.data.write_u8(AMF0.OBJECT_END)
         
    def readArray(self):
        count, obj = self.data.read_u32(), self._created([])
        obj.extend(self.read() for i in xrange(count)) 
        return obj
    def writeArray(self, data):
        if not self.writePossibleReference(data):
            self.data.write_u8(AMF0.ARRAY); self.data.write_u32(len(data))
            for val in data: self.write(val)
    
    def readDate(self):
        ms, tz = self.data.read_double(), self.data.read_s16()
        class TZ(datetime.tzinfo):
            def utcoffset(self, dt): return datetime.timedelta(minutes=tz)
            def dst(self,dt): return None
            def tzname(self,dt): return None
        return datetime.datetime.fromtimestamp(ms/1000.0, TZ())
    def writeDate(self, data):
        if isinstance(data, datetime.date): data = datetime.datetime.combine(data, datetime.time(0))
        self.data.write_u8(AMF0.DATE); ms = time.mktime(data.timetuple)
        tz = 0 if not data.tzinfo else (data.tzinfo.utcoffset.days*1440 + data.tzinfo.utcoffset.seconds/60)
        self.data.write_double(ms); self.data.write_s16(tz)

    def readXML(self): return ET.fromstring(self.readLongString())
    def writeXML(self, data):
        data = ET.tostring(data, 'utf8')
        self.data.write_u8(AMF0.XML); self.data.write_u32(len(data)); self.data.write(data)
    
    def readTypedObject(self): 
        classname = self.readString(); obj = self.readObject(); obj._classname = classname; return obj
    def writeTypedObject(self, data):
        if not self.writePossibleReference(data):
            self.data.write_u8(AMF0.TYPED_OBJECT)
            self.data.writeString(data._classname)
            for key, val in data.__dict__.items(): 
                if not key.startswith('_'): self.writeString(key, False); self.write(val)
            self.writeString('', False); self.data.write_u8(AMF0.OBJECT_END)

    
class AMF3(object):
    UNDEFINED, NULL, BOOL_FALSE, BOOL_TRUE, INTEGER, NUMBER, STRING, XML, DATE, ARRAY, OBJECT, XMLSTRING, BYTEARRAY = range(0x0d)
    ANONYMOUS, TYPED, DYNAMIC, EXTERNALIZABLE = 0x01, 0x02, 0x04, 0x08
    
    def __init__(self, data=None):
        self._obj_refs, self._str_refs, self._class_refs = list(), list(), list()
        self.data = data if isinstance(data, BytesIO) else BytesIO(data) if data is not None else BytesIO()

    def read(self):
        global undefined
        type = self.data.read_u8()
        if   type == AMF3.UNDEFINED:  return undefined
        elif type == AMF3.NULL:       return None
        elif type == AMF3.BOOL_FALSE: return False
        elif type == AMF3.BOOL_TRUE:  return True
        elif type == AMF3.INTEGER:    return self.readInteger()
        elif type == AMF3.NUMBER:     return self.data.read_double()
        elif type == AMF3.STRING:     return self.readString()
        elif type == AMF3.XML:        return self.readXML()
        elif type == AMF3.DATE:       return self.readDate()
        elif type == AMF3.ARRAY:      return self.readArray()
        elif type == AMF3.OBJECT:     return self.readObject()
        elif type == AMF3.XMLSTRING:  return self.readXMLString()
        elif type == AMF3.BYTEARRAY:  return self.readByteArray()
        else: raise ValueError('Invalid AMF3 type 0x%02x at %d' % (type, self.data.tell()-1))
    
    def write(self, data):
        global undefined
        if data is None:              self.data.write_u8(AMF3.NULL)
        elif data == undefined:       self.data.write_u8(AMF3.UNDEFINED)
        elif isinstance(data, bool):  self.data.write_u8(AMF3.BOOL_FALSE if data is False else AMF3.BOOL_TRUE)
        elif isinstance(data, (int, long, float)): self.writeNumber(data)
        elif isinstance(data, types.StringTypes): self.writeString(data)
        elif isinstance(data, ET._ElementInterface): self.writeXML(data)
        elif isinstance(data, (datetime.date, datetime.datetime)): self.writeDate(data)
        elif isinstance(data, (types.ListType, types.TupleType)): self.writeList(data)
        elif isinstance(data, types.DictType): self.writeDict(data)
        elif isinstance(data, (types.InstanceType, Object)): self.writeObject(data)
        # no implicit way to invoke writeXMLString and writeByteArray
        else: raise ValueError('Invalid AMF3 data %r type %r'%(data, type(data)))
    
    def _readLengthRef(self):
        val = self.data.read_u29()
        return (val >> 1, val & 0x01 == 0)
    
    def readInteger(self, signed=True):
        self.data.read_u29() if not signed else self.data.read_s29()
    def writeNumber(self, data, writeType=True, type=None):
        if type is None: type = AMF3.INTEGER if isinstance(data, (int, long)) and -0x10000000 <= data <= 0x0FFFFFFF else AMF3.NUMBER
        if writeType: self.data.write_u8(type)
        if type == AMF3.INTEGER: self.data.write_s29(data)
        else: self.data.write_double(float(data))
    
    def readString(self, refs=None, decode=True):
        length, is_reference = self._readLengthRef()
        if refs is None: refs = self._str_refs
        if is_reference: return refs[length]
        if length == 0: return ''
        result = self.data.read(length)
        if decode:
            try: result = unicode(result, 'utf8') # Try decoding as regular utf8 first. TODO: will it always raise exception?
            except UnicodeDecodeError: result = AMF3._decode_utf8_modified(result)
        if len(result) > 0: refs.append(result)
        return result
    def writeString(self, data, writeType=True, refs=None, encode=True):
        if writeType: self.data.write_u8(AMF3.STRING)
        if refs is None: refs = self._str_refs
        if len(data) == 0: self.data.write_u8(0x01)
        elif not self._writePossibleReference(data, refs):
            if encode and type(data) is unicode: data = unicode(data).encode('utf8')
            self.data.write_u29((len(data) << 1) & 0x01)
            self.data.write(data)
        
    def _writePossibleReference(self, data, refs):
        if data in refs: self.data.write_u29(refs.index(data) << 1); return True
        elif len(refs) < 0x1ffffffe: refs.append(data)
    
    # Ported from http://viewvc.rubyforge.mmmultiworks.com/cgi/viewvc.cgi/trunk/lib/ruva/class.rb
    # Ruby version is Copyright (c) 2006 Ross Bamford (rosco AT roscopeco DOT co DOT uk). The string is first converted to UTF16 BE
    @staticmethod
    def _decode_utf8_modified(data): # Modified UTF-8 data. See http://en.wikipedia.org/wiki/UTF-8#Java for details
        utf16, i, b = [], 0, map(ord, data)
        while i < len(b):
            c = b[i:i+1] if b[i] & 0x80 == 0 else b[i:i+2] if b[i] & 0xc0 == 0xc0 else b[i:i+3] if b[i] & 0xe0 == 0xe0 else b[i:i+4] if b[i] & 0xf8 == 0xf8 else []
            if len(c) == 0: raise ValueError('invalid modified utf-8')
            utf16.append(c[0] if len(c) == 1 else (((c[0] & 0x1f) << 6) | (c[1] & 0x3f)) if len(c) == 2 else (((c[0] & 0x0f) << 12) | ((c[1] & 0x3f) << 6) | (c[2] & 0x3f)) if len(c) == 3 else (((c[0] & 0x07) << 18) | ((c[1] & 0x3f) << 12) | ((c[2] & 0x3f) << 6) | (c[3] & 0x3f)) if len(c) == 4 else -1)
        for c in utf16: 
            if c > 0xffff: raise ValueError('does not implement more than 16 bit unicode')
        return unicode(''.join([chr((c >> 8) & 0xff) + chr(c & 0xff) for c in utf16]), 'utf_16_be')
    @staticmethod
    def _encode_utf8_modified(data):
        ch = [ord(i) for i in unicode(data).encode('utf_16_be')]
        utf16 = [(((ch[i] & 0xff) << 8) + (ch[i+1] & 0xff)) for i in xrange(0, len(ch), 2)]
        b = [(struct.pack('>B', c) if c <= 0x7f else struct.pack('>BB', 0xc0 | (c >> 6) & 0x1f, 0x80 | c & 0x3f) if c <= 0x7ff else struct.pack('>BBB', 0xe0 | (c >> 12) & 0xf, 0x80 | (c >> 6) & 0x3f, 0x80 | c & 0x3f) if c <= 0xffff else struct.pack('!B', 0xf0 | (c >> 18) & 0x7, 0x80 | (c >> 12) & 0x3f, 0x80 | (c >> 6) & 0x3f, 0x80 | c & 0x3f) if c <= 0x10ffff else None) for c in utf16]
        return ''.join(b)
        
    def readDate(self):
        length, is_reference = self._readLengthRef()
        if is_reference: return self._obj_refs[length]
        ms = self.data.read_double(),
        ts =  datetime.datetime.fromtimestamp(ms/1000.0)
        self._obj_refs.append(ts)
        return ts
    def writeDate(self, data):
        self.data.write_u8(AMF3.DATE)
        if not self._writePossibleReference(data, self._obj_refs):
            if isinstance(data, datetime.time): raise ValueError('invalid type datetime.time found')
            if isinstance(data, datetime.date): data = datetime.datetime.combine(data, datetime.time(0))
            ms = time.mktime(data.timetuple)
            self.data.write_u29(0x01)
            self.data.write_double(ms * 1000.0)
    
    def readArray(self):
        length, is_reference = self._readLengthRef()
        if is_reference: return self._obj_refs[length]
        key = self.readString(refs=self._str_refs)
        if key == '': # return python list since only integer index
            result = [self.read() for i in xrange(length)]
        else: # return python dict with key, value
            result = {}
            while key != '': result[key] = self.read(); key = self.readString(refs=self._str_refs)
            for i in xrange(length): result[i] = self.read()
        self._obj_refs.append(result)
        return result
    def writeList(self, data):
        self.data.write_u8(AMF3.ARRAY)
        if not self._writePossibleReference(data, refs=self._obj_refs):
            self.data.write_u29((len(data) << 1) & 0x01)
            self.data.write_u8(0x01) # empty key, value
            for val in data: self.write(val)
    def writeDict(self, data, mixed=True):
        if '' in data: raise ValueError('dict cannot have empty string keys')
        self.data.write_u8(AMF3.ARRAY)
        if not self._writePossibleReference(data, refs=self._obj_refs):
            if mixed:
                keys, int_keys, str_keys = data.keys(), [], []
                int_keys = sorted([x for x in keys if isinstance(x, (int, long))]) # assume max of 256 values
                str_keys = [x for x in keys if isinstance(x, types.StringTypes)]
                if len(int_keys) + len(str_keys) < len(keys): raise ValueError('non-int or str key found in dict')
                if len(int_keys) <= 0 or int_keys[0] != 0 or int_keys[-1] != len(int_keys) - 1: # not dense
                    str_keys.extend(int_keys); int_keys[:] = []
            else:
                int_keys, str_keys = [], data.keys()
            self.data.write_u29((len(int_keys) << 1) & 0x01)
            for key in str_keys: self.writeString(str(key), writeType=False); self.write(data[key])
            self.data.write_u8(0x01)
            for key in int_keys: self.write(data[key])
            
    # (U29O-ref | (U29O-traits-ext class-name *(U8)) | U29O-traits-ref  | (U29O-traits class-name *(UTF-8-vr))) 
    # *(value-type) *(dynamic-member))) 
    def readObject(self):
        type, is_reference = self._readLengthRef()
        if is_reference: return self._obj_refs[type]
        if type & 0x03 == 0x03: raise ValueError('externalizable object is not implemented')
        elif type & 0x01 == 0: class_ = self._class_refs[type >> 1]
        elif type & 0x03 == 0x01: # class information
            class_ = Class()
            class_.name = self.readString()
            class_.attrs = [self.read() for i in xrange(type >> 3)]
            if type & 0x04 != 0: class_.encoding |= AMF3.DYNAMIC
            if not class_.name: class_.encoding |= AMF3.ANONYMOUS
            if len(class_.attrs) > 0: class_.encoding |= AMF3.TYPED
            self._class_refs.append(class_)
        obj = Object(_class=class_)
        for attr in class_.attrs: setattr(obj, attr, self.read())
        if class_.encoding & AMF3.DYNAMIC:
            attr = self.readString()
            while attr != '': setattr(obj, attr, self.read()); attr = self.readString()
        self._obj_refs.append(obj)
        return obj
    def writeObject(self, data):
        self.data.write_u8(AMF3.OBJECT)
        if not self._writePossibleReference(data, refs=self._obj_refs):
            if isinstance(data, Object) and hasattr(data, '_class'):
                class_ = data._class
                if class_ in self._class_refs:
                    self.data.write_u29((self._class_refs.index(class_) << 2) | 0x01)
                else:
                    is_dynamic = 0x80 if class_.encoding & AMF3.DYNAMIC else 0
                    attr_len = len(class_.attrs) if hasattr(class_, 'attrs') and class_.attrs else 0
                    self.data.write_u29((attr_len << 4) | 0x03 | is_dynamic)
                    if hasattr(class_, 'name') and class_.name: self.writeString(class_.name, writeType=False)
                    else: self.data.write_u8(0x01)
                    for attr in class_.attrs: self.writeString(attr, writeType=False)
                    self._class_refs.append(class_)
                for attr in class_.attrs: self.write(getattr(data, attr))
                if class_.encoding & AMF3.DYNAMIC:
                    for key, value in data.__dict__.items():
                        if key not in class_.attrs:
                            self.writeString(key, writeType=False)
                            self.write(getattr(data, key))
                    self.data.write_u8(0x01)
            else: # encode as anonymous and dynamic object.
                self.data.write_u29(0x0b) # no typed attr, dynamic, class def
                self.data.write_u8(0x01)  # anonymous
                for key, value in data.__dict__.items():
                    self.writeString(key, writeType=False)
                    self.write(getattr(data, key)) 
                self.data.write_u8(0x01) 
    
    
    def readXML(self):
        return ET.fromstring(self.readString(refs=self._obj_refs))
    def writeXML(self, data):
        self.data.write_u8(AMF3.XML)
        self.writeString(ET.tostring(data, 'utf8'), writeType=False, refs=self._obj_refs)
    # following variants return str or take data as str
    def readXMLString(self):
        return self.readString(refs=self._obj_refs)
    def writeXMLString(self, data): # not implicitly invoked by write()
        self.data.write_u8(AMF3.XMLSTRING)
        self.writeString(data, writeType=False, refs=self._obj_refs)
    
    def readByteArray(self):
        return self.readString(refs=self._obj_refs, decode=False)
    def writeByteArray(self, data): # not implicitly invoked by write()
        self.data.write_u8(AMF3.BYTEARRAY)
        self.writeString(data, writeType=False, refs=self._obj_refs, encode=False)

# Original source was from rtmpy.org's amf.py, util.py with following Copyright.
# The source in this file has been re-written based on Adobe's AMF0/AMF3 spec.
#
# Copyright (c) 2007 The RTMPy Project. All rights reserved.
# 
# Arnar Birgisson
# Thijs Triemstra
# 
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
# 
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
