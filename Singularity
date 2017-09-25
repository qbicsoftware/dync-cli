Bootstrap:docker 
From:centos:7

%labels
    CREATOR Sven Fillinger
    CREATOR_MAIL sven.fillinger@qbic.uni-tuebingen.de

    SOFTWARE dync
    DESCRIPTION Python software with TCP-based data transfer using zeroMQ
    LICENSE GPL2+

%setup 
    
%post
    yum -y install epel-release
    yum -y install python34 python34-pip git
    pip3 install git+https://github.com/qbicsoftware/dync
    pip3 install tqdm

%test
    python3 --version
    dync 
