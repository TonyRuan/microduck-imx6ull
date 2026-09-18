#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Transport only. B carries the unchanged RemoteIo body protocol; I carries the
 * unchanged robotd JSON-RPC protocol. The parent owns and reaps its daemon child. */
#define CAP 131072
static int body=-1,ipc=-1,stopping=0;
static pid_t daemon_pid=-1;
static const char *ipc_path;
static void stop(int sig) {(void)sig;stopping=1;}
static int send_all(int fd,const char *p,size_t n) {
    while(n) {ssize_t k=write(fd,p,n);if(k<0 && errno==EINTR)continue;if(k<=0)return -1;p+=k;n-=k;}
    return 0;
}
static int emit(char channel,const char *p,size_t n) {
    char header[]={channel,'\t'};
    return send_all(1,header,2) || send_all(1,p,n) || send_all(1,"\n",1);
}
static void socket_options(int fd) {
    struct timeval timeout={1,0};
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
}
static int connect_ipc(void) {
    if(ipc>=0)return ipc;
    struct sockaddr_un addr={.sun_family=AF_UNIX};
    if(strlen(ipc_path)>=sizeof(addr.sun_path))return -1;
    strcpy(addr.sun_path,ipc_path);
    int fd=socket(AF_UNIX,SOCK_STREAM,0);if(fd<0)return -1;
    if(connect(fd,(struct sockaddr *)&addr,sizeof(addr))) {close(fd);return -1;}
    socket_options(fd);ipc=fd;return fd;
}
static int route(int source,char *line,size_t n) {
    if(source==0) {
        if(n==1 && line[0]=='S') {kill(daemon_pid,SIGTERM);return 0;}
        if(n==1 && line[0]=='Q') {stopping=1;return 0;}
        if(n<2 || line[1]!='\t')return -1;
        int dest=line[0]=='B'?body:line[0]=='I'?connect_ipc():-1;
        if(dest<0) {emit('E',"destination unavailable",23);return 0;}
        return send_all(dest,line+2,n-2) || send_all(dest,"\n",1);
    }
    return emit(source==1?'B':'I',line,n);
}
int main(int argc,char **argv) {
    if(argc!=4) {fprintf(stderr,"usage: hil-proxy ROBOTD PARAMS SOCKET\n");return 2;}
    ipc_path=argv[3];signal(SIGPIPE,SIG_IGN);signal(SIGTERM,stop);signal(SIGINT,stop);
    int server=socket(AF_INET,SOCK_STREAM,0),yes=1;
    setsockopt(server,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(7819),.sin_addr={htonl(INADDR_LOOPBACK)}};
    if(server<0 || bind(server,(struct sockaddr *)&addr,sizeof(addr)) || listen(server,1)) {perror("listen");return 2;}
    int log=open("robotd-hil.log",O_WRONLY|O_CREAT|O_TRUNC,0600);
    if(log<0)return 2;
    pid_t child=fork();
    if(child<0)return 2;
    if(child==0) {
        close(server);dup2(log,1);dup2(log,2);close(log);
        int nullfd=open("/dev/null",O_RDONLY);dup2(nullfd,0);close(nullfd);
        if(!getenv("DUCK_HIL_TIMINGS"))setenv("DUCK_HIL_TIMINGS","/home/debian/microduck-policy-bench/hil-timings.json",1);
        execl(argv[1],argv[1],"--sim","127.0.0.1:7819","--params",argv[2],"--socket",argv[3],(char *)NULL);
        perror("exec robotd");_exit(127);
    }
    close(log);
    daemon_pid=child;
    int status=0,reaped=0;
    char *buffers[3];size_t sizes[3]={0};
    for(int i=0;i<3;++i) {buffers[i]=malloc(CAP);if(!buffers[i])stopping=1;}
    emit('R',"ready",5);
    while(!stopping) {
        if(waitpid(child,&status,WNOHANG)==child) {reaped=1;break;}
        struct pollfd fds[4]={{0,POLLIN,0},{body,POLLIN,0},{ipc,POLLIN,0},{server,POLLIN,0}};
        int rc=poll(fds,4,1000);
        if(rc<0 && errno==EINTR)continue;
        if(rc<0)break;
        if(kill(child,0)<0)break;
        if(fds[3].revents&POLLIN) {
            int next=accept(server,NULL,NULL);
            if(body>=0)close(body);
            body=next;sizes[1]=0;socket_options(body);setsockopt(body,IPPROTO_TCP,TCP_NODELAY,&yes,sizeof(yes));
        }
        for(int i=0;i<3 && !stopping;++i) {
            if(!(fds[i].revents&(POLLIN|POLLHUP|POLLERR)))continue;
            ssize_t n=read(fds[i].fd,buffers[i]+sizes[i],CAP-sizes[i]);
            if(n<=0) {stopping=1;break;}
            sizes[i]+=(size_t)n;
            char *begin=buffers[i],*end;
            size_t remaining=sizes[i];
            while((end=memchr(begin,'\n',remaining))) {
                size_t length=(size_t)(end-begin);
                if(route(i,begin,length)) {stopping=1;break;}
                begin=end+1;remaining-=length+1;
            }
            memmove(buffers[i],begin,remaining);sizes[i]=remaining;
            if(remaining==CAP)stopping=1;
        }
    }
    if(!reaped)kill(child,SIGTERM);
    for(int i=0;!reaped && i<30;++i) {
        if(waitpid(child,&status,WNOHANG)==child) {reaped=1;break;}
        struct timespec delay={0,100000000};nanosleep(&delay,NULL);
    }
    if(!reaped) {kill(child,SIGKILL);waitpid(child,&status,0);}
    for(int i=0;i<3;++i)free(buffers[i]);
    if(body>=0)close(body);
    if(ipc>=0)close(ipc);
    close(server);emit('R',"stopped",7);return 0;
}
