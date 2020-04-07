[![Build Status](https://travis-ci.org/aseyboldt/dync.svg?branch=master)](https://travis-ci.org/aseyboldt/dync)

![dync-logo](https://github.com/qbicsoftware/dync/blob/master/dync_logo.png)

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

# Firewall requirements

Dync binds by default port 8889 for the TCP-based data transfer. Make sure to create a proper firewall rule, in order to let dync bind this port. For **server administrators**: you can always change the default port in the configuration file, by modifiying line ``address: "tcp://*:8889"`` and restart the server. Make sure to inform your clients, they have to change the port with the argument ``--port xxxx`` so the transfer can work.

# Installation

You can use pip to install both the client and the server. Dync is designed to run under **Python >= 3.3**.

## Pip for installation
Please make sure that you have **pip** for **Python 3.x** installed on your system, otherwise you will install it in your Python 2.7 environment, and execution will fail. Also make sure, that you install **Git** on your system, otherwise the installation of the development version will not work either. 

Please use the development version for the moment, until we make the first official release.

Future **official** version
```
pip install dync
```

The **development** version
```
pip install git+https://github.com/qbicsoftware/dync
```

To get a **progress bar** (optional) on the client you also need to install `tqdm`.

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

Get the public key from the dync server: `server.key` from your admin,
and store the server certificate in `~/.dync/server.key` 
(TODO we should do this more like ssh and allow different server keys
for different destinations)

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

## Targeted file upload
Normally, the dync server instance looks for a matching rule, when data arrives from valid clients and thus determines the proper destination on the server. But sometimes, you might not want that and just upload the files (maybe the files need some manual intervention first, from an PI on the server's host location). 

In this case you can use the ```passthrough``` keyword like:

```
dync -k passthrough:myID <server-hostname> <filename>
```

The files will be dropped in a dedicated directory on the server, that matches the client ID.


## Multiple file upload
If you want to upload several files at once, the most easiest way is to use ```xargs``` (https://en.wikipedia.org/wiki/Xargs). For example, you have a directory with some pdf files you want to transfer:

```
ls | grep pdf | xargs -i dync <server.url> {}
```
Or, for Mac OS X users:
```
ls | grep raw | xargs -I F dync <server.url> "F"
```

It is as easy as that. If there is an existing rule on the server for handling PDF files of course. If you have kind of manual uploads, that do not match any rule, you can do so by entering your ID like ```myID``` and can use the ```passthrough``` keyword:

```
ls | grep pdf | xargs -i dync -k passthrough:myID <server.url> {}
```
The files will then be dropped in a dedicated directory for your account only. Of course your public key must be known to the server and the ID match the correct public key, otherwise the transfer will fail.

### Upload from a list of files
Let's assume you have a text file with a list of paths:

```
/path/to/file1
/path/to/file2
/path/to/file3
```

Then you can do the same magic as before:

```
cat file_with_paths.txt | xargs -i dync -k passthrough:myID <server.url> {}
```

Or, if you want it more verbose for logging, you can use a bash script:

```
#!/bin/bash

# set the dync server url
SERVER_URL=the.url.de

rc=$?

if [ "$1" == ""  ]; then
       echo "You need to provide the path to the file list"
       rc=1
       exit $rc
fi

FILE_LIST=$1

echo "Initiating upload with dync ..."

while read p; do
        echo "Transfer of $p starts ..."
        dync -k passthrough:myID "$SERVER_URL" "$p"
done <$FILE_LIST
exit $rc
```

Then you can just call the script like:

```
./scriptname.sh file_list.txt
```


## Directory upload
Dync does not support directory uploads, but you can use ```tar``` (https://www.gnu.org/software/tar/) to
bundle the contents into an archive first. In this case you need
to explicitly set the remote filename with `-n`:

```
tar -c <dir> | dync -n <filename-on-server.tar> <server>
```

In this case, the archive will be not extracted automatically on the server.

However, if you want to do that, you can use the ```untar``` keyword:

```
tar -c <dir> | dync -n <filename-on-server.tar> -k untar:True <server>
```

This is currently restricted to a maximum archive member size of 10 files, in order to prevent the server from beeing busy with extracting only.

for more information see `dync -h`.

# Server usage

## Add a user

to add a user just go to the home folder of the user which is running the dync server.
Change into the .dync/clients folder there an add the public key of the new user.
The key should look like this:
```
#   ****  Generated on date by pyzmq  ****
#   ZeroMQ CURVE Public Certificate
#   Exchange securely, or use a secure mechanism to verify the contents
#   of this file after exchange. Store public certificates in your home
#   directory, in the .curve subdirectory.

metadata
curve
    user_id = "user"
    public-key = "superfancykey"

```


## Certificates

## Customize server side file storage

## Create a systemd service

A simple service file for systemd can look like this:

```
> cat /etc/systemd/system/dync.service
[Unit]
Description=Dync server for receiving files and forward them to dropboxes

[Service]
Type=simple
ExecStart=/usr/bin/dync-server
```

After that you have to ```sudo systemctl daemon-reload``` and then you can start the
dync-server service with ```sudo systemctl start dync```


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
