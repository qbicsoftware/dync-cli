[![Build Status](https://travis-ci.org/aseyboldt/dync.svg?branch=master)](https://travis-ci.org/aseyboldt/dync)

# dync

*This is work in progress!*

`dync` and `dync-server` are programs that transfer files and metadata
to a central server. It was written at QBiC to send raw scientific
data from labs to our servers.

- No data without metadata: The server rejects file uploads that
  do not contain necessary information.
- Data integrity. The client computes a checksum while uploading
  the file. The server stores the checksum alongside the data.
  The server keeps partial uploads separate from finished files to
  prevent mix-ups.
- `dync` uses curvezmq as implemented in zeromq with pre-shared long
  term keys for transport security to provide confidentiality
  and forward secrecy.
- Reasonable performance and availability for large files (tested up
  to 1TB). The client initiates uploads, but the server
  controls the flow of data. If too many clients try to upload
  files at the same time, it tells some of the clients
  to wait until there is capacity for the upload. In preliminary
  tests we reach an upload speed of about 200MB/s
  on a 10Gbit connection and 120MB/s on a 1Gbit ethernet.
  The throughput on 10Gbit could probably be improved somewhat.
- `dync` works on shaky networks. It reconnects if the tcp
  connection times out and retransmits messages that were lost
  between the tcp connections or because of errors that slipped through
  the error detection of tcp. I can use it to copy files of several GB
  from my home wireless, which is an achievement most people can
  not hope to appreciate (https://xkcd.com/1457/).

`dync` is named after Dynein, a motor protein that moves vesicles and
other cargo in cells.

# Installation

You can use pip to install both the client and the server
(TODO, not uploaded yet):

```
pip install dync
```

or for the development version
```
pip install git+https://github.com/aseyboldt/dync
```

To get a progress bar on the client you also need to install `tqdm`.

# Client usage

Each client has to create certificates and send them to the server
before uploading files. You can use this command
to create a client certificate in the current directory:

```
python -c "import zmq.auth; zmq.auth.create_certificates('.', 'client')"
```

This will create two files: `client.key` and `client.key_secret`. Send
the first to the admin of the server on a secure channel. Put the other
one into a directory `.dync` in your home dir and set permissions:

```
chmod 700 ~/.dync
chmod 600 ~/.dync/client.key_secret
```

Store the server certificate in `~/.dync/server.key` (TODO we should
do this more like ssh and allow different server keys for different
destinations)

Once the server admin approved your keys you can upload files:

```
dync <server-hostname> <filename>
```

If you have metadata in a json-file you can attach it like this:

```
dync -m <path-to-meta> <server-hostname> <filename>
```

To overwrite key-value pairs in the metadata use the `-k` switch:

```
dync -k my_id:ABCDE -k sample:FOO <server-hostname> <filename>
```

dync does not support directory uploads, but you can use tar to
bundle the contents into an archive first. In this case you need
to explicitly set the remote filename with `-n`:

```
tar -c <dir> | dync -n <filename-on-server.tar> <server>
```

for more information see `dync -h`.

# Server usage

## Certificates

## Customize server side file storage

# Related software

# Performance

# Protocol

`dync` uses zeromq to send messages between server and client. The server
binds a ROUTER socket to port 8889 (TODO, which port should we use?),
the client connects with a DEALER socket. The client socket identity must
be unique for each upload, eg an uuit4. Both set the zmq security
mechanism to `CURVE` and provide appropriate keys.

All messages are encoded as multipart messages, where the first frame is
a command and the other frames are arguments. All integers are in big
endian byteorder. Strings (filenames and json data) are encoded in utf8.
Error messages provide an error code and an error message. The error
codes should follow the html error codes where possible.

#### Client messages:

```
post-file: <flags: u32> <filename: utf8> <meta: utf8 json>
post-chunk: <flags: u32> <seek: u64> <data: bytes> [<checksum: bytes>]
query-status:
error: <code: u32> <msg: utf8>
```

#### Server messages:

```
upload-approved: <credit: u32> <chunksize: u32> <maxqueue: u32>
transfer-credit: <amount: u32>
status-report: <seek: u64> <credit: u32>
upload-finished: <upload_id: utf8>
error: <code: u32> <msg: utf8>
```

The client initiates an upload by sending a `post-file` message. The
flags field is not used at the moment and should be set to 0. It also
provides a utf8 encoded filename and a json object as metadata. The
server can reject the upload with an `error` message (eg. if the client
did not provide reasonable metadata) or approve it by sending an
`upload-approved` message. This message also tells the client how may
chunks it may send to the server (the credit), how large the chunks
should be at most (chunksize) and how many chunks the client should
keep in memory after sending them in case the connection is lost and
chunks need to be sent again.

#### Client behaviour

The client connects a zmq `DEALER` socket with `curve` security and
a random uuid as `IDENTITY` to the server.

After sending a `post-file` and receiving a `upload-approved` message
the client adds chunks to the zmq send queue until it runs of of credit
or reaches the last chunk. It keeps the last `maxqueue` chunks in
memory.

The last chunk is signalled by setting the least significant bit in
the flags of the `post-chunk` message. The message must also include
a sha256 checksum of the whole file.

Once the client does not have any credit left, it waits for a
`transfer-credit`, `status-report`, `upload-finished` or `error`
message from the server. If it does not recieve any it sends a
`query-status` message after a timeout and waits again. After a
number of unsuccessful retries it sends an error message and aborts
the upload.

On recieving a `transfer-credit` message the client sends chunks from
the position in the file where it left of. If it recieves a
`status-report`, it resets the internal seed to the position specified
in the server message and resends chunks. A well behaving server will
not request chunks older than `maxqueue` (the maximum credit it will
give to a client), thus the client can use the old chunks that are
still in memory.

![Client state machine](/doc/client.png?raw=true "Client state machine")

#### Server behaviour

![Server state machine](/doc/server.png?raw=true "Server state machine")
