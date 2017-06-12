# Copyright (c) 2007-2009, Mamta Singh. All rights reserved. see README for details.
# Copyright (c) 2010-2011, Kundan Singh.

'''
This is a simple implementation of a Flash RTMP server to accept connections and stream requests. The module is organized as follows:
1. The FlashServer class is the main class to provide the server abstraction. It uses the multitask module for co-operative multitasking.
   It also uses the App abstract class to implement the applications.
2. The Server class implements a simple server to receive new Client connections and inform the FlashServer application. The Client class
   derived from Protocol implements the RTMP client functions. The Protocol class implements the base RTMP protocol parsing. A Client contains
   various streams from the client, represented using the Stream class.
3. The Message, Header and Command represent RTMP message, header and command respectively. The FLV class implements functions to perform read
   and write of FLV file format.


Typically an application can launch this server as follows:
$ python rtmp.py

To know the command line options use the -h option:
$ python rtmp.py -h

To start the server with a different directory for recording and playing FLV files from, use the following command.
$ python rtmp.py -r some-other-directory/
Note the terminal '/' in the directory name. Without this, it is just used as a prefix in FLV file names.

A test client is available in testClient directory, and can be compiled using Flex Builder. Alternatively, you can use the SWF file to launch
from testClient/bin-debug after starting the server. Once you have launched the client in the browser, you can connect to
local host by clicking on 'connect' button. Then click on publish button to publish a stream. Open another browser with
same URL and first connect then play the same stream name. If everything works fine you should be able to see the video
from first browser to the second browser. Similar, in the first browser, if you check the record box before publishing,
it will create a new FLV file for the recorded stream. You can close the publishing stream and play the recorded stream to
see your recording. Note that due to initial delay in timestamp (in case publish was clicked much later than connect),
your played video will start appearing after some initial delay.


If an application wants to use this module as a library, it can launch the server as follows:
>>> agent = FlashServer()   # a new RTMP server instance
>>> agent.root = 'flvs/'    # set the document root to be 'flvs' directory. Default is current './' directory.
>>> agent.start()           # start the server
>>> multitask.run()         # this is needed somewhere in the application to actually start the co-operative multitasking.


If an application wants to specify a different application other than the default App, it can subclass it and supply the application by
setting the server's apps property. The following example shows how to define "myapp" which invokes a 'connected()' method on client when
the client connects to the server.

class MyApp(App):         # a new MyApp extends the default App in rtmp module.
    def __init__(self):   # constructor just invokes base class constructor
        App.__init__(self)
    def onConnect(self, client, *args):
        result = App.onConnect(self, client, *args)   # invoke base class method first
        def invokeAdded(self, client):                # define a method to invoke 'connected("some-arg")' on Flash client
            yield client.call('connected', 'some-arg')
        multitask.add(invokeAdded(self, client))      # need to invoke later so that connection is established before callback
        return result     # return True to accept, or None to postpone calling accept()
...
agent.apps = dict({'myapp': MyApp, 'someapp': MyApp, '*': App})

Now the client can connect to rtmp://server/myapp or rtmp://server/someapp and will get connected to this MyApp application.
If the client doesn't define "function connected(arg:String):void" in the NetConnection.client object then the server will
throw an exception and display the error message.

'''

import os, sys, time, struct, socket, traceback, multitask, amf, hashlib, hmac, random

_debug = False

class ConnectionClosed:
    'raised when the client closed the connection'

def truncate(data, max=100):
    return data and len(data)>max and data[:max] + '...(%d)'%(len(data),) or data
    
class SockStream(object):
    '''A class that represents a socket as a stream'''
    def __init__(self, sock):
        self.sock, self.buffer = sock, ''
        self.bytesWritten = self.bytesRead = 0
    
    def close(self):
        self.sock.close()
        
    def read(self, count):
        try:
            while True:
                if len(self.buffer) >= count: # do have enough data in buffer
                    data, self.buffer = self.buffer[:count], self.buffer[count:]
                    raise StopIteration(data)
                if _debug: print 'socket.read[%d] calling recv()'%(count,)
                data = (yield multitask.recv(self.sock, 4096)) # read more from socket
                if not data: raise ConnectionClosed
                if _debug: print 'socket.read[%d] %r'%(len(data), truncate(data))
                self.bytesRead += len(data)
                self.buffer += data
        except StopIteration: raise
        except: raise ConnectionClosed # anything else is treated as connection closed.
        
    def unread(self, data):
        self.buffer = data + self.buffer
            
    def write(self, data):
        while len(data) > 0: # write in 4K chunks each time
            chunk, data = data[:4096], data[4096:]
            self.bytesWritten += len(chunk)
            if _debug: print 'socket.write[%d] %r'%(len(chunk), truncate(chunk))
            try: yield multitask.send(self.sock, chunk)
            except: raise ConnectionClosed
                                

'''
NOTE: Here is a part of the documentation to understand how the Chunks' headers work.
      To have a complete documentation, YOU HAVE TO READ rtmp_specification_1.0.pdf (from page 13)

This is the format of a chunk. Here, we store all except the chunk data:
------------------------------------------------------------------------

+-------------+----------------+-------------------+--------------+
| Basic header|Chunk Msg Header|Extended Time Stamp|   Chunk Data |
+-------------+----------------+-------------------+--------------+

This are the formats of the basic header:
-----------------------------------------

 0 1 2 3 4 5 6 7      0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5      0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3
+-+-+-+-+-+-+-+-+    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|fmt|   cs id   |    |fmt|     0     |   cs id - 64  |    |fmt|     1     |        cs id - 64             | 
+-+-+-+-+-+-+-+-+    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

  (cs id < 64)            (64 <= cs id < 320)                           (320 <= cs id)

fmt store the format of the chunk message header. There are four different formats.


Type 0 (fmt=00):
----------------

This type MUST be used at the start of a chunk stream, and whenever the stream timestamp goes backward (e.g., because of a backward seek).

 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      timestamp                |                message length                 |message type id|                message stream id              |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+


Type 1 (fmt=01):
----------------

Streams with variable-sized messages (for example, many video formats) SHOULD use this format for the first chunk of each new message after the first.

 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5  
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                timestamp delta                |                message length                 |message type id|
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+


Type 2 (fmt=10):
----------------

Streams with constant-sized messages (for example, some audio and data formats) SHOULD use this format for the first chunk of each message after the first. 

 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3   
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                timestamp delta                |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+


Type 3 (fmt=11):
----------------

Chunks of Type 3 have no header. Stream ID, message length and timestamp delta are not present; chunks of this type take values from
the preceding chunk. When a single message is split into chunks, all chunks of a message except the first one, SHOULD use this type.

Extended Timestamp:
-------------------

This field is transmitted only when the normal time stamp in the chunk message header is set to 0x00ffffff. If normal time stamp is 
set to any value less than 0x00ffffff, this field MUST NOT be present. This field MUST NOT be present if the timestamp field is not 
present. Type 3 chunks MUST NOT have this field. This field if transmitted is located immediately after the chunk message header
and before the chunk data. 

'''
class Header(object):
    # Chunk type 0 = FULL
    # Chunk type 1 = MESSAGE
    # Chunk type 2 = TIME
    # Chunk type 3 = SEPARATOR
    FULL, MESSAGE, TIME, SEPARATOR, MASK = 0x00, 0x40, 0x80, 0xC0, 0xC0
    
    def __init__(self, channel=0, time=0, size=None, type=None, streamId=0):
        
        self.channel = channel   # in fact, this will be the fmt + cs id
        self.time = time         # timestamp[delta]
        self.size = size         # message length
        self.type = type         # message type id
        self.streamId = streamId # message stream id
        
        if (channel < 64): self.hdrdata = struct.pack('>B', channel)
        elif (channel < 320): self.hdrdata = '\x00' + struct.pack('>B', channel-64)
        else: self.hdrdata = '\x01' + struct.pack('>H', channel-64)
    
    def toBytes(self, control):
        data = chr(ord(self.hdrdata[0]) | control)
        if len(self.hdrdata) >= 2: data += self.hdrdata[1:] 
        
        # if the chunk type is not 3
        if control != Header.SEPARATOR:
            data += struct.pack('>I', self.time if self.time < 0xFFFFFF else 0xFFFFFF)[1:] # add time in 3 bytes
            # if the chunk type is not 2
            if control != Header.TIME:
                data += struct.pack('>I', self.size)[1:] # add size in 3 bytes
                data += struct.pack('>B', self.type) # add type in 1 byte
                # if the chunk type is not 1
                if control != Header.MESSAGE:
                    data += struct.pack('<I', self.streamId) # add streamId in little-endian 4 bytes
            # add the extended time part to the header if timestamp[delta] >= 16777215
            if self.time >= 0xFFFFFF:
                data += struct.pack('>I', self.time)
        return data

    def __repr__(self):
        return ("<Header channel=%r time=%r size=%r type=%s (%r) streamId=%r>"
            % (self.channel, self.time, self.size, Message.type_name.get(self.type, 'unknown'), self.type, self.streamId))
    
    def dup(self):
        return Header(channel=self.channel, time=self.time, size=self.size, type=self.type, streamId=self.streamId)


class Message(object):
    # message types: RPC3, DATA3,and SHAREDOBJECT3 are used with AMF3
    CHUNK_SIZE,   ABORT,   ACK,   USER_CONTROL, WIN_ACK_SIZE, SET_PEER_BW, AUDIO, VIDEO, DATA3, SHAREDOBJ3, RPC3, DATA, SHAREDOBJ, RPC, AGGREGATE = \
    0x01,         0x02,    0x03,  0x04,         0x05,         0x06,        0x08,  0x09,  0x0F,  0x10,       0x11, 0x12, 0x13,      0x14, 0x16
    type_name = dict(enumerate('unknown chunk-size abort ack user-control win-ack-size set-peer-bw unknown audio video unknown unknown unknown unknown unknown data3 sharedobj3 rpc3 data sharedobj rpc unknown aggregate'.split()))
    
    def __init__(self, hdr=None, data=''):
        self.header, self.data = hdr or Header(), data
    
    # define properties type, streamId and time to access self.header.(property)
    for p in ['type', 'streamId', 'time']:
        exec 'def _g%s(self): return self.header.%s'%(p, p)
        exec 'def _s%s(self, %s): self.header.%s = %s'%(p, p, p, p)
        exec '%s = property(fget=_g%s, fset=_s%s)'%(p, p, p)
    @property
    def size(self): return len(self.data)
            
    def __repr__(self):
        return ("<Message header=%r data=%r>"% (self.header, truncate(self.data)))
    
    def dup(self):
        return Message(self.header.dup(), self.data[:])
                
class Protocol(object):
    PING_SIZE, DEFAULT_CHUNK_SIZE, HIGH_WRITE_CHUNK_SIZE, PROTOCOL_CHANNEL_ID = 1536, 128, 4096, 2 # constants
    READ_WIN_SIZE, WRITE_WIN_SIZE = 1000000L, 1073741824L
    
    def __init__(self, sock):
        self.stream = SockStream(sock)
        self.lastReadHeaders, self.incompletePackets, self.lastWriteHeaders = dict(), dict(), dict()
        self.readChunkSize = self.writeChunkSize = Protocol.DEFAULT_CHUNK_SIZE
        self.readWinSize0, self.readWinSize, self.writeWinSize0, self.writeWinSize = 0L, self.READ_WIN_SIZE, 0L, self.WRITE_WIN_SIZE
        self.nextChannelId = Protocol.PROTOCOL_CHANNEL_ID + 1
        self._time0 = time.time()
        self.writeQueue = multitask.Queue()
            
    @property
    def relativeTime(self):
        return int(1000*(time.time() - self._time0))
    
    def messageReceived(self, msg): # override in subclass
        yield
            
    def protocolMessage(self, msg):
        if msg.type == Message.ACK: # respond to ACK requests
            self.writeWinSize0 = struct.unpack('>L', msg.data)[0]
#            response = Message()
#            response.type, response.data = msg.type, msg.data
#            yield self.writeMessage(response)
        elif msg.type == Message.CHUNK_SIZE:
            self.readChunkSize = struct.unpack('>L', msg.data)[0]
            if _debug: print "set read chunk size to %d" % self.readChunkSize
        elif msg.type == Message.WIN_ACK_SIZE:
            self.readWinSize, self.readWinSize0 = struct.unpack('>L', msg.data)[0], self.stream.bytesRead
        elif msg.type == Message.USER_CONTROL:
            type, data = struct.unpack('>H', msg.data[:2])[0], msg.data[2:]
            if type == 3: # client expects a response when it sends set buffer length
                streamId, bufferTime = struct.unpack('>II', data)
                response = Message()
                response.time, response.type, response.data = self.relativeTime, Message.USER_CONTROL, struct.pack('>HI', 0, streamId)
                yield self.writeMessage(response)
        yield
        
    def connectionClosed(self):
        yield
                            
    def parse(self):
        try:
            yield self.parseCrossDomainPolicyRequest() # check for cross domain policy
            yield self.parseHandshake()  # parse rtmp handshake
            yield self.parseMessages()   # parse messages
        except ConnectionClosed:
            yield self.connectionClosed()
            if _debug: print 'parse connection closed'
        except:
            if _debug: print 'exception, closing connection'
            if _debug: traceback.print_exc()
            yield self.connectionClosed()
                    
    def writeMessage(self, message):
        yield self.writeQueue.put(message)
            
    def parseCrossDomainPolicyRequest(self):
        # read the request
        REQUEST = '<policy-file-request/>\x00'
        data = (yield self.stream.read(len(REQUEST)))
        if data == REQUEST:
            if _debug: print data
            data = '''<!DOCTYPE cross-domain-policy SYSTEM "http://www.macromedia.com/xml/dtds/cross-domain-policy.dtd">
                    <cross-domain-policy>
                      <allow-access-from domain="*" to-ports="1935" secure='false'/>
                    </cross-domain-policy>'''
            yield self.stream.write(data)
            raise ConnectionClosed
        else:
            yield self.stream.unread(data)
            
    SERVER_KEY = '\x47\x65\x6e\x75\x69\x6e\x65\x20\x41\x64\x6f\x62\x65\x20\x46\x6c\x61\x73\x68\x20\x4d\x65\x64\x69\x61\x20\x53\x65\x72\x76\x65\x72\x20\x30\x30\x31\xf0\xee\xc2\x4a\x80\x68\xbe\xe8\x2e\x00\xd0\xd1\x02\x9e\x7e\x57\x6e\xec\x5d\x2d\x29\x80\x6f\xab\x93\xb8\xe6\x36\xcf\xeb\x31\xae'
    FLASHPLAYER_KEY = '\x47\x65\x6E\x75\x69\x6E\x65\x20\x41\x64\x6F\x62\x65\x20\x46\x6C\x61\x73\x68\x20\x50\x6C\x61\x79\x65\x72\x20\x30\x30\x31\xF0\xEE\xC2\x4A\x80\x68\xBE\xE8\x2E\x00\xD0\xD1\x02\x9E\x7E\x57\x6E\xEC\x5D\x2D\x29\x80\x6F\xAB\x93\xB8\xE6\x36\xCF\xEB\x31\xAE'
    
    def parseHandshake(self):
        '''Parses the rtmp handshake'''
        data = (yield self.stream.read(Protocol.PING_SIZE + 1)) # bound version and first ping
        data = Protocol.handshakeResponse(data)
        yield self.stream.write(data)
        data = (yield self.stream.read(Protocol.PING_SIZE))
    
    @staticmethod
    def handshakeResponse(data):
        # send both data parts before reading next ping-size, to work with ffmpeg
        if struct.unpack('>I', data[5:9])[0] == 0:
            data = '\x03' + '\x00'*Protocol.PING_SIZE
            return data + data[1:]
        else:
            type, data = ord(data[0]), data[1:] # first byte is ignored
            scheme = None
            for s in range(0, 2):
                digest_offset = (sum([ord(data[i]) for i in range(772, 776)]) % 728 + 776) if s == 1 else (sum([ord(data[i]) for i in range(8, 12)]) % 728 + 12)
                temp = data[0:digest_offset] + data[digest_offset+32:Protocol.PING_SIZE]
                hash = Protocol._calculateHash(temp, Protocol.FLASHPLAYER_KEY[:30])
                if hash == data[digest_offset:digest_offset+32]:
                    scheme = s
                    break
            if scheme is None:
                if _debug: print 'invalid RTMP connection data, assuming scheme 0'
                scheme = 0
            client_dh_offset = (sum([ord(data[i]) for i in range(768, 772)]) % 632 + 8) if scheme == 1 else (sum([ord(data[i]) for i in range(1532, 1536)]) % 632 + 772)
            outgoingKp = data[client_dh_offset:client_dh_offset+128]
            handshake = struct.pack('>IBBBB', 0, 1, 2, 3, 4) + ''.join([chr(random.randint(0, 255)) for i in xrange(Protocol.PING_SIZE-8)])
            server_dh_offset = (sum([ord(handshake[i]) for i in range(768, 772)]) % 632 + 8) if scheme == 1 else (sum([ord(handshake[i]) for i in range(1532, 1536)]) % 632 + 772)
            keys = Protocol._generateKeyPair() # (public, private)
            handshake = handshake[:server_dh_offset] + keys[0][0:128] + handshake[server_dh_offset+128:]
            if type > 0x03: raise Exception('encryption is not supported')
            server_digest_offset = (sum([ord(handshake[i]) for i in range(772, 776)]) % 728 + 776) if scheme == 1 else (sum([ord(handshake[i]) for i in range(8, 12)]) % 728 + 12)
            temp = handshake[0:server_digest_offset] + handshake[server_digest_offset+32:Protocol.PING_SIZE]
            hash = Protocol._calculateHash(temp, Protocol.SERVER_KEY[:36])
            handshake = handshake[:server_digest_offset] + hash + handshake[server_digest_offset+32:]
            buffer = data[:Protocol.PING_SIZE-32]
            key_challenge_offset = (sum([ord(buffer[i]) for i in range(772, 776)]) % 728 + 776) if scheme == 1 else (sum([ord(buffer[i]) for i in range(8, 12)]) % 728 + 12)
            challenge_key = data[key_challenge_offset:key_challenge_offset+32]
            hash = Protocol._calculateHash(challenge_key, Protocol.SERVER_KEY[:68])
            rand_bytes = ''.join([chr(random.randint(0, 255)) for i in xrange(Protocol.PING_SIZE-32)])
            last_hash = Protocol._calculateHash(rand_bytes, hash[:32])
            output = chr(type) + handshake + rand_bytes + last_hash
            return output
        
    @staticmethod
    def _calculateHash(msg, key): # Hmac-sha256
        return hmac.new(key, msg, hashlib.sha256).digest()
        
    @staticmethod
    def _generateKeyPair(): # dummy key pair since we don't support encryption
        return (''.join([chr(random.randint(0, 255)) for i in xrange(128)]), '')
        
    def parseMessages(self):
        '''Parses complete messages until connection closed. Raises ConnectionLost exception.'''
        CHANNEL_MASK = 0x3F
        while True:
            hdrsize = ord((yield self.stream.read(1))[0])  # read header size byte
            channel = hdrsize & CHANNEL_MASK
            if channel == 0: # we need one more byte
                channel = 64 + ord((yield self.stream.read(1))[0])
            elif channel == 1: # we need two more bytes
                data = (yield self.stream.read(2))
                channel = 64 + ord(data[0]) + 256 * ord(data[1])

            hdrtype = hdrsize & Header.MASK   # read header type byte
            if hdrtype == Header.FULL or not self.lastReadHeaders.has_key(channel):
                header = Header(channel)
                self.lastReadHeaders[channel] = header
            else:
                header = self.lastReadHeaders[channel]
            
            if hdrtype < Header.SEPARATOR: # time or delta has changed
                data = (yield self.stream.read(3))
                header.time = struct.unpack('!I', '\x00' + data)[0]
                
            if hdrtype < Header.TIME: # size and type also changed
                data = (yield self.stream.read(3))
                header.size = struct.unpack('!I', '\x00' + data)[0]
                header.type = ord((yield self.stream.read(1))[0])

            if hdrtype < Header.MESSAGE: # streamId also changed
                data = (yield self.stream.read(4))
                header.streamId = struct.unpack('<I', data)[0]

            if header.time == 0xFFFFFF: # if we have extended timestamp, read it
                data = (yield self.stream.read(4))
                header.extendedTime = struct.unpack('!I', data)[0]
                if _debug: print 'extended time stamp', '%x'%(header.extendedTime,)
            else:
                header.extendedTime = None
                
            if hdrtype == Header.FULL:
                header.currentTime = header.extendedTime or header.time
                header.hdrtype = hdrtype
            elif hdrtype in (Header.MESSAGE, Header.TIME):
                header.hdrtype = hdrtype

            #print header.type, '0x%02x'%(hdrtype,), header.time, header.currentTime
            
            # if _debug: print 'R', header, header.currentTime, header.extendedTime, '0x%x'%(hdrsize,)
             
            data = self.incompletePackets.get(channel, "") # are we continuing an incomplete packet?
            
            count = min(header.size - (len(data)), self.readChunkSize) # how much more
            
            data += (yield self.stream.read(count))

            # check if we need to send Ack
            if self.readWinSize is not None:
                if self.stream.bytesRead > (self.readWinSize0 + self.readWinSize):
                    self.readWinSize0 = self.stream.bytesRead
                    ack = Message()
                    ack.time, ack.type, ack.data = self.relativeTime, Message.ACK, struct.pack('>L', self.readWinSize0)
                    yield self.writeMessage(ack)
                    
            if len(data) < header.size: # we don't have all data
                self.incompletePackets[channel] = data
            else: # we have all data
                if hdrtype in (Header.MESSAGE, Header.TIME):
                    header.currentTime = header.currentTime + (header.extendedTime or header.time)
                elif hdrtype == Header.SEPARATOR:
                    if header.hdrtype in (Header.MESSAGE, Header.TIME):
                        header.currentTime = header.currentTime + (header.extendedTime or header.time)
                if len(data) == header.size:
                    if channel in self.incompletePackets:
                        del self.incompletePackets[channel]
                        if _debug:
                            print 'aggregated %r bytes message: readChunkSize(%r) x %r'%(len(data), self.readChunkSize, len(data) / self.readChunkSize)
                else:
                    data, self.incompletePackets[channel] = data[:header.size], data[header.size:]
                
                hdr = Header(channel=header.channel, time=header.currentTime, size=header.size, type=header.type, streamId=header.streamId)
                msg = Message(hdr, data)

                if hdr.type == Message.AGGREGATE:
                    ''' see http://code.google.com/p/red5/source/browse/java/server/trunk/src/org/red5/server/net/rtmp/event/Aggregate.java / getParts()
                    '''
                    if _debug: print 'Protocol.parseMessages aggregated msg=', msg 
                    aggdata = data;
                    while len(aggdata) > 0:
                        '''
                        type=1 byte
                        size=3 bytes
                        time=4 bytes
                        streamId= 4 bytes
                        data= size bytes
                        backPointer=4 bytes, value == size
                        '''
                        subtype = ord(aggdata[0])
                        subsize = struct.unpack('!I', '\x00' + aggdata[1:4])[0]
                        subtime = struct.unpack('!I', aggdata[4:8])[0]
                        substreamid = struct.unpack('<I', aggdata[8:12])[0]     
                        subheader = Header(channel, time=subtime, size=subsize, type=subtype, streamId=substreamid) # TODO: set correct channel
                        aggdata = aggdata[11:] # skip header       
                        submsgdata = aggdata[:subsize] # get message data 
                        submsg = Message(subheader, submsgdata) 
                        
                        yield self.parseMessage(submsg)
                                        
                        aggdata = aggdata[subsize:] # skip message data
                    
                        backpointer = struct.unpack('!I', aggdata[0:4])[0]
                        if backpointer != subsize:
                            print 'Warning aggregate submsg backpointer=%r != %r' % (backpointer, subsize)                          
                        aggdata = aggdata[4:] # skip back pointer, go to next message
                else:
                    yield self.parseMessage(msg)
                

    def parseMessage(self, msg):
        try:            
            if _debug: print 'Protocol.parseMessage msg=', msg            
            if msg.header.channel == Protocol.PROTOCOL_CHANNEL_ID:
                yield self.protocolMessage(msg)
            else: 
                yield self.messageReceived(msg)
        except:
            if _debug: print 'Protocol.parseMessage exception', (traceback and traceback.print_exc() or None)

    def write(self):
        '''Writes messages to stream'''
        while True:
#            while self.writeQueue.empty(): (yield multitask.sleep(0.01))
#            message = self.writeQueue.get() # TODO this should be used using multitask.Queue and remove previous wait.
            message = yield self.writeQueue.get() # TODO this should be used using multitask.Queue and remove previous wait.
            if _debug: print 'Protocol.write msg=', message
            if message is None: 
                try: self.stream.close()  # just in case TCP socket is not closed, close it.
                except: pass
                break
            
            # get the header stored for the stream
            if self.lastWriteHeaders.has_key(message.streamId):
                header = self.lastWriteHeaders[message.streamId]
            else:
                if self.nextChannelId <= Protocol.PROTOCOL_CHANNEL_ID: self.nextChannelId = Protocol.PROTOCOL_CHANNEL_ID+1
                header, self.nextChannelId = Header(self.nextChannelId), self.nextChannelId + 1
                self.lastWriteHeaders[message.streamId] = header
            if message.type < Message.AUDIO:
                header = Header(Protocol.PROTOCOL_CHANNEL_ID)
               
            # now figure out the header data bytes
            if header.streamId != message.streamId or header.time == 0 or message.time <= header.time:
                header.streamId, header.type, header.size, header.time, header.delta = message.streamId, message.type, message.size, message.time, message.time
                control = Header.FULL
            elif header.size != message.size or header.type != message.type:
                header.type, header.size, header.time, header.delta = message.type, message.size, message.time, message.time-header.time
                control = Header.MESSAGE
            else:
                header.time, header.delta = message.time, message.time-header.time
                control = Header.TIME
            
            hdr = Header(channel=header.channel, time=header.delta if control in (Header.MESSAGE, Header.TIME) else header.time, size=header.size, type=header.type, streamId=header.streamId)
            assert message.size == len(message.data)

            data = ''
            while len(message.data) > 0:
                data += hdr.toBytes(control) # gather header bytes
                count = min(self.writeChunkSize, len(message.data))
                data += message.data[:count]
                message.data = message.data[count:]
                control = Header.SEPARATOR # incomplete message continuation
            try:
                yield self.stream.write(data)
            except ConnectionClosed:
                yield self.connectionClosed()
            except:
                print traceback.print_exc()

class Command(object):
    ''' Class for command / data messages'''
    def __init__(self, type=Message.RPC, name=None, id=None, tm=0, cmdData=None, args=[]):
        '''Create a new command with given type, name, id, cmdData and args list.'''
        self.type, self.name, self.id, self.time, self.cmdData, self.args = type, name, id, tm, cmdData, args[:]
        
    def __repr__(self):
        return ("<Command type=%r name=%r id=%r data=%r args=%r>" % (self.type, self.name, self.id, self.cmdData, self.args))
    
    def setArg(self, arg):
        self.args.append(arg)
    
    def getArg(self, index):
        return self.args[index]
    
    @classmethod
    def fromMessage(cls, message):
        ''' initialize from a parsed RTMP message'''
        assert (message.type in [Message.RPC, Message.RPC3, Message.DATA, Message.DATA3])

        length = len(message.data)
        if length == 0: raise ValueError('zero length message data')
        
        if message.type == Message.RPC3 or message.type == Message.DATA3:
            assert message.data[0] == '\x00' # must be 0 in AMF3
            data = message.data[1:]
        else:
            data = message.data
        
        amfReader = amf.AMF0(data)

        inst = cls()
        inst.type = message.type
        inst.time = message.time
        inst.name = amfReader.read() # first field is command name

        try:
            if message.type == Message.RPC or message.type == Message.RPC3:
                inst.id = amfReader.read() # second field *may* be message id
                inst.cmdData = amfReader.read() # third is command data
            else:
                inst.id = 0
            inst.args = [] # others are optional
            while True:
                inst.args.append(amfReader.read())
        except EOFError:
            pass
        return inst
    
    def toMessage(self):
        msg = Message()
        assert self.type
        msg.type = self.type
        msg.time = self.time
        output = amf.BytesIO()
        amfWriter = amf.AMF0(output)
        amfWriter.write(self.name)
        if msg.type == Message.RPC or msg.type == Message.RPC3:
            amfWriter.write(self.id)
            amfWriter.write(self.cmdData)
        for arg in self.args:
            amfWriter.write(arg)
        output.seek(0)
        #hexdump.hexdump(output)
        #output.seek(0)
        if msg.type == Message.RPC3 or msg.type == Message.DATA3:
            data = '\x00' + output.read()
        else:
            data = output.read()
        msg.data = data
        output.close()
        return msg

def getfilename(path, name, root):
    '''return the file name for the given stream. The name is derived as root/scope/name.flv where scope is
    the the path present in the path variable.'''
    ignore, ignore, scope = path.partition('/')
    if scope: scope = scope + '/'
    result = root + scope + name + '.flv'
    if _debug: print 'filename=', result
    return result

class FLV(object):
    '''An FLV file which converts between RTMP message and FLV tags.'''
    def __init__(self):
        self.fname = self.fp = self.type = None
        self.tsp = self.tsr = 0; self.tsr0 = None
    
    def open(self, path, type='read', mode=0775):
        '''Open the file for reading (type=read) or writing (type=record or append).'''
        if str(path).find('/../') >= 0 or str(path).find('\\..\\') >= 0: raise ValueError('Must not contain .. in name')
        if _debug: print 'opening file', path
        self.tsp = self.tsr = 0; self.tsr0 = None; self.tsr1 = 0; self.type = type
        if type in ('record', 'append'):
            try: os.makedirs(os.path.dirname(path), mode)
            except: pass
            if type == 'record' or not os.path.exists(path): # if file does not exist, use record mode
                self.fp = open(path, 'w+b')
                self.fp.write('FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00') # the header and first previousTagSize
                self.writeDuration(0.0)
            else:
                self.fp = open(path, 'r+b')
                self.fp.seek(-4, os.SEEK_END)
                ptagsize, = struct.unpack('>I', self.fp.read(4))
                self.fp.seek(-4-ptagsize, os.SEEK_END)
                bytes = self.fp.read(ptagsize)
                type, len0, len1, ts0, ts1, ts2, sid0, sid1 = struct.unpack('>BBHBHBBH', bytes[:11])
                ts = (ts0 << 16) | (ts1 & 0x0ffff) | (ts2 << 24)
                self.tsr1 = ts + 20; # some offset after the last packet
                self.fp.seek(0, os.SEEK_END)
        else: 
            self.fp = open(path, 'rb')
            magic, version, flags, offset = struct.unpack('!3sBBI', self.fp.read(9))
            if _debug: print 'FLV.open() hdr=', magic, version, flags, offset
            if magic != 'FLV': raise ValueError('This is not a FLV file')
            if version != 1: raise ValueError('Unsupported FLV file version')
            if offset > 9: self.fp.seek(offset-9, os.SEEK_CUR)
            self.fp.read(4) # ignore first previous tag size
        return self 
    
    def close(self):
        '''Close the underlying file for this object.'''
        if _debug: print 'closing flv file'
        if self.type in ('record', 'append') and self.tsr0 is not None:
            self.writeDuration((self.tsr - self.tsr0)/1000.0)
        if self.fp is not None: 
            try: self.fp.close()
            except: pass
            self.fp = None
    
    def delete(self, path):
        '''Delete the underlying file for this object.'''
        try: os.unlink(path)
        except: pass
        
    def writeDuration(self, duration):
        if _debug: print 'writing duration', duration
        output = amf.BytesIO()
        amfWriter = amf.AMF0(output) # TODO: use AMF3 if needed
        amfWriter.write('onMetaData')
        amfWriter.write({"duration": duration, "videocodecid": 2})
        output.seek(0); data = output.read()
        length, ts = len(data), 0
        data = struct.pack('>BBHBHB', Message.DATA, (length >> 16) & 0xff, length & 0x0ffff, (ts >> 16) & 0xff, ts & 0x0ffff, (ts >> 24) & 0xff) + '\x00\x00\x00' +  data
        data += struct.pack('>I', len(data))
        lastpos = self.fp.tell()
        if lastpos != 13: self.fp.seek(13, os.SEEK_SET)
        self.fp.write(data)
        if lastpos != 13: self.fp.seek(lastpos, os.SEEK_SET)
        
    def write(self, message):
        '''Write a message to the file, assuming it was opened for writing or appending.'''
#        if message.type == Message.VIDEO:
#            self.videostarted = True
#        elif not hasattr(self, "videostarted"): return
        if message.type == Message.AUDIO or message.type == Message.VIDEO:
            length, ts = message.size, message.time
            #if _debug: print 'FLV.write()', message.type, ts
            if self.tsr0 is None: self.tsr0 = ts - self.tsr1
            self.tsr, ts = ts, ts - self.tsr0
            # if message.type == Message.AUDIO: print 'w', message.type, ts
            data = struct.pack('>BBHBHB', message.type, (length >> 16) & 0xff, length & 0x0ffff, (ts >> 16) & 0xff, ts & 0x0ffff, (ts >> 24) & 0xff) + '\x00\x00\x00' +  message.data
            data += struct.pack('>I', len(data))
            self.fp.write(data)
    
    def reader(self, stream):
        '''A generator to periodically read the file and dispatch them to the stream. The supplied stream
        object must have a send(Message) method and id and client properties.'''
        if _debug: print 'reader started'
        yield
        try:
            while self.fp is not None:
                bytes = self.fp.read(11)
                if len(bytes) == 0:
                    try: tm = stream.client.relativeTime
                    except: tm = 0
                    response = Command(name='onStatus', id=stream.id, tm=tm, args=[amf.Object(level='status',code='NetStream.Play.Stop', description='File ended', details=None)])
                    yield stream.send(response.toMessage())
                    break
                type, len0, len1, ts0, ts1, ts2, sid0, sid1 = struct.unpack('>BBHBHBBH', bytes)
                length = (len0 << 16) | len1; ts = (ts0 << 16) | (ts1 & 0x0ffff) | (ts2 << 24)
                body = self.fp.read(length); ptagsize, = struct.unpack('>I', self.fp.read(4))
                if ptagsize != (length+11): 
                    if _debug: print 'invalid previous tag-size found:', ptagsize, '!=', (length+11),'ignored.'
                if stream is None or stream.client is None: break # if it is closed
                #hdr = Header(3 if type == Message.AUDIO else 4, ts if ts < 0xffffff else 0xffffff, length, type, stream.id)
                hdr = Header(0, ts, length, type, stream.id)
                msg = Message(hdr, body)
                # if _debug: print 'FLV.read() length=', length, 'hdr=', hdr
                # if hdr.type == Message.AUDIO: print 'r', hdr.type, hdr.time
                if type == Message.DATA: # metadata
                    amfReader = amf.AMF0(body) # TODO: use AMF3 if needed
                    name = amfReader.read()
                    obj = amfReader.read()
                    if _debug: print 'FLV.read()', name, repr(obj)
                yield stream.send(msg)
                if ts > self.tsp: 
                    diff, self.tsp = ts - self.tsp, ts
                    if _debug: print 'FLV.read() sleep', diff
                    yield multitask.sleep(diff / 1000.0)
        except StopIteration: pass
        except: 
            if _debug: print 'closing the reader', (sys and sys.exc_info() or None)
            if self.fp is not None: 
                try: self.fp.close()
                except: pass
                self.fp = None
            
    def seek(self, offset):
        '''For file reader, try seek to the given time. The offset is in millisec'''
        if self.type == 'read':
            if _debug: print 'FLV.seek() offset=', offset, 'current tsp=', self.tsp
            self.fp.seek(0, os.SEEK_SET)
            magic, version, flags, length = struct.unpack('!3sBBI', self.fp.read(9))
            if length > 9: self.fp.seek(length-9, os.SEEK_CUR)
            self.fp.seek(4, os.SEEK_CUR) # ignore first previous tag size
            self.tsp, ts = int(offset), 0
            while self.tsp > 0 and ts < self.tsp:
                bytes = self.fp.read(11)
                if not bytes: break
                type, len0, len1, ts0, ts1, ts2, sid0, sid1 = struct.unpack('>BBHBHBBH', bytes)
                length = (len0 << 16) | len1; ts = (ts0 << 16) | (ts1 & 0x0ffff) | (ts2 << 24)
                self.fp.seek(length, os.SEEK_CUR)
                ptagsize, = struct.unpack('>I', self.fp.read(4))
                if ptagsize != (length+11): break
            if _debug: print 'FLV.seek() new ts=', ts, 'tell', self.fp.tell()
                
        
class Stream(object):
    '''The stream object that is used for RTMP stream.'''
    count = 0;
    def __init__(self, client):
        self.client, self.id, self.name = client, 0, ''
        self.recordfile = self.playfile = None # so that it doesn't complain about missing attribute
        self.queue = multitask.Queue()
        self._name = 'Stream[' + str(Stream.count) + ']'; Stream.count += 1
        if _debug: print self, 'created'
        
    def close(self):
        if _debug: print self, 'closing'
        if self.recordfile is not None: self.recordfile.close(); self.recordfile = None
        if self.playfile is not None: self.playfile.close(); self.playfile = None
        self.client = None # to clear the reference
        pass
    
    def __repr__(self):
        return self._name;
    
    def recv(self):
        '''Generator to receive new Message on this stream, or None if stream is closed.'''
        return self.queue.get()
    
    def send(self, msg):
        '''Method to send a Message or Command on this stream.'''
        if isinstance(msg, Command):
            msg = msg.toMessage()
        msg.streamId = self.id
        # if _debug: print self,'send'
        if self.client is not None: yield self.client.writeMessage(msg)
        
class Client(Protocol):
    '''The client object represents a single connected client to the server.'''
    def __init__(self, sock, server):
        Protocol.__init__(self, sock)
        self.server, self.agent, self.streams, self._nextCallId, self._nextStreamId, self.objectEncoding = \
          server,      None,         {},           2,                1,                  0.0
        self.queue = multitask.Queue() # receive queue used by application
        multitask.add(self.parse()); multitask.add(self.write())

    def recv(self):
        '''Generator to receive new Message (msg, arg) on this stream, or (None,None) if stream is closed.'''
        return self.queue.get()
    
    def connectionClosed(self):
        '''Called when the client drops the connection'''
        if _debug: 'Client.connectionClosed'
        yield self.writeMessage(None)
        yield self.queue.put((None,None))
            
    def messageReceived(self, msg):
        if (msg.type == Message.RPC or msg.type == Message.RPC3) and msg.streamId == 0:
            cmd = Command.fromMessage(msg)
            # if _debug: print 'rtmp.Client.messageReceived cmd=', cmd
            if cmd.name == 'connect':
                self.agent = cmd.cmdData
                if _debug: print 'connect', ', '.join(['%s=%r'%(x, getattr(self.agent, x)) for x in 'app flashVer swfUrl tcUrl fpad capabilities audioCodecs videoCodecs videoFunction pageUrl objectEncoding'.split() if hasattr(self.agent, x)])
                self.objectEncoding = self.agent.objectEncoding if hasattr(self.agent, 'objectEncoding') else 0.0
                yield self.server.queue.put((self, cmd.args)) # new connection
            elif cmd.name == 'createStream':
                response = Command(name='_result', id=cmd.id, tm=self.relativeTime, type=self.rpc, args=[self._nextStreamId])
                yield self.writeMessage(response.toMessage())
                
                stream = Stream(self) # create a stream object
                stream.id = self._nextStreamId
                self.streams[self._nextStreamId] = stream
                self._nextStreamId += 1

                yield self.queue.put(('stream', stream)) # also notify others of our new stream
            elif cmd.name == 'closeStream':
                assert msg.streamId in self.streams
                yield self.streams[msg.streamId].queue.put(None) # notify closing to others
                del self.streams[msg.streamId]
            else:
                # if _debug: print 'Client.messageReceived cmd=', cmd
                yield self.queue.put(('command', cmd)) # RPC call
        else: # this has to be a message on the stream
            assert msg.streamId != 0
            assert msg.streamId in self.streams
            # if _debug: print self.streams[msg.streamId], 'recv'
            stream = self.streams[msg.streamId]
            if not stream.client: stream.client = self 
            yield stream.queue.put(msg) # give it to stream

    @property
    def rpc(self):
        # TODO: reverting r141 since it causes exception in setting self.rpc
        return Message.RPC if self.objectEncoding == 0.0 else Message.RPC3
    
    def accept(self):
        '''Method to accept an incoming client.'''
        response = Command()
        response.id, response.name, response.type = 1, '_result', self.rpc
        if _debug: print 'Client.accept() objectEncoding=', self.objectEncoding
        arg = amf.Object(level='status', code='NetConnection.Connect.Success',
                         description='Connection succeeded.', fmsVer='rtmplite/8,2')
        if hasattr(self.agent, 'objectEncoding'):
            arg.objectEncoding, arg.details = self.objectEncoding, None
        response.setArg(arg)
        yield self.writeMessage(response.toMessage())
            
    def rejectConnection(self, reason=''):
        '''Method to reject an incoming client.'''
        response = Command()
        response.id, response.name, response.type = 1, '_error', self.rpc
        response.setArg(amf.Object(level='status', code='NetConnection.Connect.Rejected',
                        description=reason, fmsVer='rtmplite/8,2', details=None))
        yield self.writeMessage(response.toMessage())
            
    def redirectConnection(self, url, reason='Connection failed'):
        '''Method to redirect an incoming client to the given url.'''
        response = Command()
        response.id, response.name, response.type = 1, '_error', self.rpc
        extra = dict(code=302, redirect=url)
        response.setArg(amf.Object(level='status', code='NetConnection.Connect.Rejected',
                        description=reason, fmsVer='rtmplite/8,2', details=None, ex=extra))
        yield self.writeMessage(response.toMessage())

    def call(self, method, *args):
        '''Call a (callback) method on the client.'''
        cmd = Command()
        cmd.id, cmd.time, cmd.name, cmd.type = self._nextCallId, self.relativeTime, method, self.rpc
        cmd.args, cmd.cmdData = args, None
        self._nextCallId += 1
        if _debug: print 'Client.call method=', method, 'args=', args, ' msg=', cmd.toMessage()
        yield self.writeMessage(cmd.toMessage())
            
    def createStream(self):
        ''' Create a stream on the server side'''
        stream = Stream(self)
        stream.id = self._nextStreamId
        self.streams[stream.id] = stream
        self._nextStreamId += 1
        return stream


class Server(object):
    '''A RTMP server listens for incoming connections and informs the app.'''
    def __init__(self, sock):
        '''Create an RTMP server on the given bound TCP socket. The server will terminate
        when the socket is disconnected, or some other error occurs in listening.'''
        self.sock = sock
        self.queue = multitask.Queue()  # queue to receive incoming client connections
        multitask.add(self.run())

    def recv(self):
        '''Generator to wait for incoming client connections on this server and return
        (client, args) or (None, None) if the socket is closed or some error.'''
        return self.queue.get()
        
    def run(self):
        try:
            while True:
                sock, remote = (yield multitask.accept(self.sock))  # receive client TCP
                if sock == None:
                    if _debug: print 'rtmp.Server accept(sock) returned None.' 
                    break
                if _debug: print 'connection received from', remote
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) # make it non-block
                client = Client(sock, self)
        except GeneratorExit: pass # terminate
        except: 
            if _debug: print 'rtmp.Server exception ', (sys and sys.exc_info() or None)
        
        if (self.sock):
            try: self.sock.close(); self.sock = None
            except: pass
        if (self.queue):
            yield self.queue.put((None, None))
            self.queue = None

class App(object):
    '''An application instance containing any number of streams. Except for constructor all methods are generators.'''
    count = 0
    def __init__(self):
        self.name = str(self.__class__.__name__) + '[' + str(App.count) + ']'; App.count += 1
        self.players, self.publishers, self._clients = {}, {}, [] # Streams indexed by stream name, and list of clients
        if _debug: print self.name, 'created'
    def __del__(self):
        if _debug: print self.name, 'destroyed'
    @property
    def clients(self):
        '''everytime this property is accessed it returns a new list of clients connected to this instance.'''
        return self._clients[1:] if self._clients is not None else []
    def onConnect(self, client, *args):
        if _debug: print self.name, 'onConnect', client.path
        return True
    def onDisconnect(self, client):
        if _debug: print self.name, 'onDisconnect', client.path
    def onPublish(self, client, stream):
        if _debug: print self.name, 'onPublish', client.path, stream.name
    def onClose(self, client, stream):
        if _debug: print self.name, 'onClose', client.path, stream.name
    def onPlay(self, client, stream):
        if _debug: print self.name, 'onPlay', client.path, stream.name
    def onStop(self, client, stream):
        if _debug: print self.name, 'onStop', client.path, stream.name
    def onCommand(self, client, cmd, *args):
        if _debug: print self.name, 'onCommand', cmd, args
    def onStatus(self, client, info):
        if _debug: print self.name, 'onStatus', info
    def onResult(self, client, result):
        if _debug: print self.name, 'onResult', result
    def onPublishData(self, client, stream, message): # this is invoked every time some media packet is received from published stream.
        return True # should return True so that the data is actually published in that stream
    def onPlayData(self, client, stream, message):
        return True # should return True so that data will be actually played in that stream
    def getfile(self, path, name, root, mode):
        if mode == 'play':
            path = getfilename(path, name, root)
            if not os.path.exists(path): return None
            return FLV().open(path)
        elif mode in ('record', 'append'):
            path = getfilename(path, name, root)
            return FLV().open(path, mode)
#        elif stream.mode == 'live': FLV().delete(path) # TODO: this is commented out to avoid accidental delete
        return None

class Wirecast(App):
    '''A wrapper around App to workaround with wirecast publisher which does not send AVC seq periodically. It defines new stream variables
    such as in publish stream 'metaData' to store first published metadata Message, and 'avcSeq' to store the last published AVC seq Message,
    and in play stream 'avcIntra' to indicate if AVC intra frame has been sent or not. These variables are created onPublish and onPlay.
    Additional, when onPlay it also also sends any published stream.metaData if found in associated publisher. When onPlayData for video, if
    it detects AVC seq it sets avcIntra so that it is not explicitly sent. This is the case with Flash Player publisher. When onPlayData for video,
    if it detects avcIntra is not set, it discards the packet until AVC NALU or seq is received. If NALU is received but previous seq is not received
    it uses the publisher's avcSeq message to send before this NALU if found.'''
    def __init__(self):
        App.__init__(self)

    def onPublish(self, client, stream):
        App.onPublish(self, client, stream)
        if not hasattr(stream, 'metaData'): stream.metaData = None
        if not hasattr(stream, 'avcSeq'): stream.avcSeq = None
        
    def onPlay(self, client, stream):
        App.onPlay(self, client, stream)
        if not hasattr(stream, 'avcIntra'): stream.avcIntra = False
        publisher = self.publishers.get(stream.name, None)
        if publisher and publisher.metaData: # send published meta data to this player joining late
            multitask.add(stream.send(publisher.metaData.dup()))
    
    def onPublishData(self, client, stream, message):
        if message.type == Message.DATA and not stream.metaData: # store the first meta data on this published stream for late joining players
            stream.metaData = message.dup()
        if message.type == Message.VIDEO and message.data[:2] == '\x17\x00': # H264Avc intra + seq, store it
            stream.avcSeq = message.dup()
        return True

    def onPlayData(self, client, stream, message):
        if message.type == Message.VIDEO: # only video packets need special handling
            if message.data[:2] == '\x17\x00': # intra+seq is being sent, possibly by Flash Player publisher.
                stream.avcIntra = True
            elif not stream.avcIntra:  # intra frame hasn't been sent yet.
                if message.data[:2] == '\x17\x01': # intra+nalu is being sent, possibly by wirecast publisher.
                    publisher = self.publishers.get(stream.name, None)
                    if publisher and publisher.avcSeq: # if a publisher exists
                        def sendboth(stream, msgs):
                            stream.avcIntra = True
                            for msg in msgs: yield stream.send(msg)
                        multitask.add(sendboth(stream, [publisher.avcSeq.dup(), message]))
                        return False # so that caller doesn't send it again
                return False # drop until next intra video is sent
        return True

class FlashServer(object):
    '''A RTMP server to record and stream Flash video.'''
    def __init__(self):
        '''Construct a new FlashServer. It initializes the local members.'''
        self.sock = self.server = None;
        self.apps = dict({'*': App, 'wirecast': Wirecast}) # supported applications: * means any as in {'*': App}
        self.clients = dict()  # list of clients indexed by scope. First item in list is app instance.
        self.root = '';
        
    def start(self, host='0.0.0.0', port=1935):
        '''This should be used to start listening for RTMP connections on the given port, which defaults to 1935.'''
        if not self.server:
            sock = self.sock = socket.socket(type=socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            if _debug: print 'listening on ', sock.getsockname()
            sock.listen(5)
            server = self.server = Server(sock) # start rtmp server on that socket
            multitask.add(self.serverlistener())
    
    def stop(self):
        if _debug: print 'stopping Flash server'
        if self.server and self.sock:
            try: self.sock.close(); self.sock = None
            except: pass
        self.server = None
        
    def serverlistener(self):
        '''Server listener (generator). It accepts all connections and invokes client listener'''
        try:
            while True:  # main loop to receive new connections on the server
                client, args = (yield self.server.recv()) # receive an incoming client connection.
                # TODO: we should reject non-localhost client connections.
                if not client:                # if the server aborted abnormally,
                    break                     #    hence close the listener.
                if _debug: print 'client connection received', client, args
                if client.objectEncoding != 0 and client.objectEncoding != 3:
                #if client.objectEncoding != 0:
                    yield client.rejectConnection(reason='Unsupported encoding ' + str(client.objectEncoding) + '. Please use NetConnection.defaultObjectEncoding=ObjectEncoding.AMF0')
                    yield client.connectionClosed()
                else:
                    client.path = str(client.agent.app) if hasattr(client.agent, 'app') else str(client.agent['app']) if isinstance(client.agent, dict) else None
                    if not client.path:
                        yield client.rejectConnection(reason='Missing app path')
                        break
                    name, ignore, scope = client.path.partition('/')
                    if '*' not in self.apps and name not in self.apps:
                        yield client.rejectConnection(reason='Application not found: ' + name)
                    else: # create application instance as needed and add in our list
                        if _debug: print 'name=', name, 'name in apps', str(name in self.apps)
                        app = self.apps[name] if name in self.apps else self.apps['*'] # application class
                        if client.path in self.clients: inst = self.clients[client.path][0]
                        else: inst = app()
                        
                        win_ack = Message()
                        win_ack.time, win_ack.type, win_ack.data = client.relativeTime, Message.WIN_ACK_SIZE, struct.pack('>L', client.writeWinSize)
                        yield client.writeMessage(win_ack)
                        
#                        set_peer_bw = Message()
#                        set_peer_bw.time, set_peer_bw.type, set_peer_bw.data = client.relativeTime, Message.SET_PEER_BW, struct.pack('>LB', client.writeWinSize, 1)
#                        client.writeMessage(set_peer_bw)
                        
                        try: 
                            result = inst.onConnect(client, *args)
                        except: 
                            if _debug: print sys.exc_info()
                            yield client.rejectConnection(reason='Exception on onConnect'); 
                            continue
                        if result is True or result is None:
                            if client.path not in self.clients: 
                                self.clients[client.path] = [inst]; inst._clients=self.clients[client.path]
                            self.clients[client.path].append(client)
                            if result is True:
                                yield client.accept() # TODO: else how to kill this task when rejectConnection() later
                            multitask.add(self.clientlistener(client)) # receive messages from client.
                        else: 
                            yield client.rejectConnection(reason='Rejected in onConnect')
        except GeneratorExit: pass # terminate
        except StopIteration: raise
        except: 
            if _debug: print 'serverlistener exception', traceback.print_exc()
            
    def clientlistener(self, client):
        '''Client listener (generator). It receives a command and invokes client handler, or receives a new stream and invokes streamlistener.'''
        try:
            while True:
                msg, arg = (yield client.recv())   # receive new message from client
                if not msg:                   # if the client disconnected,
                    if _debug: print 'connection closed from client'
                    break                     #    come out of listening loop.
                if msg == 'command':          # handle a new command
                    multitask.add(self.clienthandler(client, arg))
                elif msg == 'stream':         # a new stream is created, handle the stream.
                    arg.client = client
                    multitask.add(self.streamlistener(arg))
        except StopIteration: raise
        except:
            if _debug: print 'clientlistener exception', (sys and sys.exc_info() or None)
        
        try:
            # client is disconnected, clear our state for application instance.
            if _debug: print 'cleaning up client', client.path
            inst = None
            if client.path in self.clients:
                inst = self.clients[client.path][0]
                self.clients[client.path].remove(client)
            for stream in client.streams.values(): # for all streams of this client
                self.closehandler(stream)
            client.streams.clear() # and clear the collection of streams
            if client.path in self.clients and len(self.clients[client.path]) == 1: # no more clients left, delete the instance.
                if _debug: print 'removing the application instance'
                inst = self.clients[client.path][0]
                inst._clients = None
                del self.clients[client.path]
            if inst is not None: inst.onDisconnect(client)
        except: 
            if _debug: print 'clientlistener exception', (sys and sys.exc_info() or None)
            
    def closehandler(self, stream):
        '''A stream is closed explicitly when a closeStream command is received from given client.'''
        if stream.client is not None:
            inst = self.clients[stream.client.path][0]
            if stream.name in inst.publishers and inst.publishers[stream.name] == stream: # clear the published stream
                inst.onClose(stream.client, stream)
                del inst.publishers[stream.name]
            if stream.name in inst.players and stream in inst.players[stream.name]:
                inst.onStop(stream.client, stream)
                inst.players[stream.name].remove(stream)
                if len(inst.players[stream.name]) == 0:
                    del inst.players[stream.name]
            stream.close()
        
    def clienthandler(self, client, cmd):
        '''A generator to handle a single command on the client.'''
        inst = self.clients[client.path][0]
        if inst:
            if cmd.name == '_error':
                if hasattr(inst, 'onStatus'):
                    result = inst.onStatus(client, cmd.args[0])
            elif cmd.name == '_result':
                if hasattr(inst, 'onResult'):
                    result = inst.onResult(client, cmd.args[0])
            else:
                res, code, result = Command(), '_result', None
                try:
                    result = inst.onCommand(client, cmd.name, *cmd.args)
                except:
                    if _debug: print 'Client.call exception', (sys and sys.exc_info() or None) 
                    code = '_error'
                args = (result,) if result is not None else dict()
                res.id, res.time, res.name, res.type = cmd.id, client.relativeTime, code, client.rpc
                res.args, res.cmdData = args, None
                if _debug: print 'Client.call method=', code, 'args=', args, ' msg=', res.toMessage()
                yield client.writeMessage(res.toMessage())
        yield
        
    def streamlistener(self, stream):
        '''Stream listener (generator). It receives stream message and invokes streamhandler.'''
        try:
            stream.recordfile = None # so that it doesn't complain about missing attribute
            while True:
                msg = (yield stream.recv())
                if not msg:
                    if _debug: print 'stream closed'
                    self.closehandler(stream)
                    break
                # if _debug: msg
                multitask.add(self.streamhandler(stream, msg))
        except: 
            if _debug: print 'streamlistener exception', (sys and sys.exc_info() or None)
            
    def streamhandler(self, stream, message):
        '''A generator to handle a single message on the stream.'''
        try:
            if message.type == Message.RPC or message.type == Message.RPC3:
                cmd = Command.fromMessage(message)
                if _debug: print 'streamhandler received cmd=', cmd
                if cmd.name == 'publish':
                    yield self.publishhandler(stream, cmd)
                elif cmd.name == 'play':
                    yield self.playhandler(stream, cmd)
                elif cmd.name == 'closeStream':
                    self.closehandler(stream)
                elif cmd.name == 'seek':
                    yield self.seekhandler(stream, cmd) 
            else: # audio or video message
                yield self.mediahandler(stream, message)
        except GeneratorExit: pass
        except StopIteration: raise
        except: 
            if _debug: print 'exception in streamhandler', (sys and sys.exc_info())
    
    def publishhandler(self, stream, cmd):
        '''A new stream is published. Store the information in the application instance.'''
        try:
            stream.mode = 'live' if len(cmd.args) < 2 else cmd.args[1] # live, record, append
            stream.name = cmd.args[0]
            if _debug: print 'publishing stream=', stream.name, 'mode=', stream.mode
            if stream.name and '?' in stream.name: stream.name = stream.name.partition('?')[0]
            inst = self.clients[stream.client.path][0]
            if (stream.name in inst.publishers):
                raise ValueError, 'Stream name already in use'
            inst.publishers[stream.name] = stream # store the client for publisher
            inst.onPublish(stream.client, stream)
            
            stream.recordfile = inst.getfile(stream.client.path, stream.name, self.root, stream.mode)
            response = Command(name='onStatus', id=cmd.id, tm=stream.client.relativeTime, args=[amf.Object(level='status', code='NetStream.Publish.Start', description='', details=None)])
            yield stream.send(response)
        except ValueError, E: # some error occurred. inform the app.
            if _debug: print 'error in publishing stream', str(E)
            response = Command(name='onStatus', id=cmd.id, tm=stream.client.relativeTime, args=[amf.Object(level='error',code='NetStream.Publish.BadName',description=str(E),details=None)])
            yield stream.send(response)

    def playhandler(self, stream, cmd):
        '''A new stream is being played. Just updated the players list with this stream.'''
        try:
            inst = self.clients[stream.client.path][0]
            name = stream.name = cmd.args[0]  # store the stream's name
            if stream.name and '?' in stream.name: name = stream.name = stream.name.partition('?')[0]
            start = cmd.args[1] if len(cmd.args) >= 2 else -2
            if name not in inst.players:
                inst.players[name] = [] # initialize the players for this stream name
            if stream not in inst.players[name]: # store the stream as players of this name
                inst.players[name].append(stream)
            task = None
            if start >= 0 or start == -2 and name not in inst.publishers:
                stream.playfile = inst.getfile(stream.client.path, stream.name, self.root, 'play')
                if stream.playfile:
                    if start > 0: stream.playfile.seek(start)
                    task = stream.playfile.reader(stream)
                elif start >= 0: raise ValueError, 'Stream name not found'
            if _debug: print 'playing stream=', name, 'start=', start
            inst.onPlay(stream.client, stream)
            
            # Default chunk size is 128. It is pretty small when we stream high audio and video quality.
            # So, send the choosen chunk size to flash client.
            stream.client.writeChunkSize = Protocol.HIGH_WRITE_CHUNK_SIZE
            m0 = Message() # SetChunkSize
            m0.time, m0.type, m0.data = stream.client.relativeTime, Message.CHUNK_SIZE, struct.pack('>L', stream.client.writeChunkSize)
            yield stream.client.writeMessage(m0)
            
#            m1 = Message() # UserControl/StreamIsRecorded
#            m1.time, m1.type, m1.data = stream.client.relativeTime, Message.USER_CONTROL, struct.pack('>HI', 4, stream.id)
#            yield stream.client.writeMessage(m1)
            
            m2 = Message() # UserControl/StreamBegin
            m2.time, m2.type, m2.data = stream.client.relativeTime, Message.USER_CONTROL, struct.pack('>HI', 0, stream.id)
            yield stream.client.writeMessage(m2)
            
#            response = Command(name='onStatus', id=cmd.id, args=[amf.Object(level='status',code='NetStream.Play.Reset', description=stream.name, details=None)])
#            yield stream.send(response)
            
            response = Command(name='onStatus', id=cmd.id, tm=stream.client.relativeTime, args=[amf.Object(level='status',code='NetStream.Play.Start', description=stream.name, details=None)])
            yield stream.send(response)
            
#            response = Command(name='onStatus', id=cmd.id, tm=stream.client.relativeTime, args=[amf.Object(level='status',code='NetStream.Play.PublishNotify', description=stream.name, details=None)])
#            yield stream.send(response)
            
            if task is not None: multitask.add(task)
        except ValueError, E: # some error occurred. inform the app.
            if _debug: print 'error in playing stream', str(E)
            response = Command(name='onStatus', id=cmd.id, tm=stream.client.relativeTime, args=[amf.Object(level='error',code='NetStream.Play.StreamNotFound',description=str(E),details=None)])
            yield stream.send(response)
            
    def seekhandler(self, stream, cmd):
        '''A stream is seeked to a new position. This is allowed only for play from a file.'''
        try:
            offset = cmd.args[0]
            if stream.playfile is None or stream.playfile.type != 'read': 
                raise ValueError, 'Stream is not seekable'
            stream.playfile.seek(offset)
            response = Command(name='onStatus', id=cmd.id, tm=stream.client.relativeTime, args=[amf.Object(level='status',code='NetStream.Seek.Notify', description=stream.name, details=None)])
            yield stream.send(response)
        except ValueError, E: # some error occurred. inform the app.
            if _debug: print 'error in seeking stream', str(E)
            response = Command(name='onStatus', id=cmd.id, tm=stream.client.relativeTime, args=[amf.Object(level='error',code='NetStream.Seek.Failed',description=str(E),details=None)])
            yield stream.send(response)
            
    def mediahandler(self, stream, message):
        '''Handle incoming media on the stream, by sending to other stream in this application instance.'''
        if stream.client is not None:
            inst = self.clients[stream.client.path][0]
            result = inst.onPublishData(stream.client, stream, message)
            if result:
                for s in (inst.players.get(stream.name, [])):
                    #if _debug: print 'D', stream.name, s.name
                    m = message.dup()
                    result = inst.onPlayData(s.client, s, m)
                    if result:
                        yield s.send(m)
                if stream.recordfile is not None:
                    stream.recordfile.write(message)

# The main routine to start, run and stop the service
if __name__ == '__main__':
    from optparse import OptionParser
    parser = OptionParser(version='SVN $Revision$, $Date$'.replace('$', ''))
    parser.add_option('-i', '--host',    dest='host',    default='0.0.0.0', help="listening IP address. Default '0.0.0.0'")
    parser.add_option('-p', '--port',    dest='port',    default=1935, type="int", help='listening port number. Default 1935')
    parser.add_option('-r', '--root',    dest='root',    default='./',       help="document path prefix. Directory must end with /. Default './'")
    parser.add_option('-d', '--verbose', dest='verbose', default=False, action='store_true', help='enable debug trace')
    (options, args) = parser.parse_args()
    
    _debug = options.verbose
    try:
        agent = FlashServer()
        agent.root = options.root
        agent.start(options.host, options.port)
        if _debug: print time.asctime(), 'Flash Server Starts - %s:%d' % (options.host, options.port)
        multitask.run()
    except KeyboardInterrupt:
        pass
    if _debug: print time.asctime(), 'Flash Server Stops'
