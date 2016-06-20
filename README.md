# dync

*This is work in progress!*

`dync` and `dync-server` are programs that transfer files and metadata
to a central server. It was written at QBiC to send raw scientific
data from labs to our servers. Design goals:

- No data without metadata: The server rejects file uploads that
  do not contain necessary information.
- Data integrity. The client computes a checksum while uploading
  the file. The server stores the checksum alongside the data.
  The server keeps partial uploads separate from finished files to
  prevent mix-ups.
- `dync` uses curvezmq as implemented in zeromq with pre-shared long
  term keys for transport security to provide confidentiality
  and forward secrecy.
- Reasonable performance and availability for large files.
  The client initiates uploads, but the server
  controls the flow of data. If too many clients try to upload
  files at the same time, the it tells some of the clients
  to wait until there is capacity for the upload. In preliminary
  tests we reach an upload speed of about 200MB/s
  on a 10Gbit connection and 120MB/s on a 1Gbit ethernet.
  The throughput on 10Gbit could probably be improved somewhat.
- `dync` works on shaky networks. It reconnects if the tcp
  connection times out and retransmitts messages that were lost
  between the tcp connections. I can use it to copy files of several GB
  from my home wireless, which is an achievement most people can
  not hope to appreciate (https://xkcd.com/1457/).

`dync` is named after Dynein, a motor protein that moves vesicels and
other cargo in all your cells.

# Installation

You can use pip to install both the client and the server
(TODO, not uploaded yet):

```
pip install dync
```

To get a progress bar on the client you also need to install `tqdm`.

# Client usag
# Installation

You can use pip to install both the client and the server
(TODO, not uploaded yet):

```
pip install dync
```

To get a progress bar on the client you also need to install `tqdm`.

# Client usagee

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
dync -k my_id:ABCDE sample:FOO <server-hostname> <filename>
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
