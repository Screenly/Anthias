#! /bin/sh

### BEGIN INIT INFO
# Provides:          Prometheus Node Exporter
# Required-Start:    $remote_fs $syslog
# Required-Stop:     $remote_fs $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Start daemon at boot time
# Description:       Enable service provided by daemon.
### END INIT INFO

. /lib/lsb/init-functions

NAME="node_exporter"

# Read configuration variable file if it is present
[ -r /etc/default/$NAME ] && . /etc/default/$NAME

BIN='/usr/local/bin/node_exporter'
PID_FILE="/var/run/$NAME.pid"
CWD=`pwd`
USER='nobody'

start () {
        log_daemon_msg "Starting $NAME"
        if start-stop-daemon --start --chuid $USER --chdir /tmp --quiet --oknodo --pidfile "$PID_FILE" -b -m -N 19 --exec $BIN; then
                log_end_msg 0
        else
                log_end_msg 1
        fi
}

stop () {
        start-stop-daemon --stop --quiet --oknodo --pidfile "$PID_FILE"
}

status () {
        status_of_proc -p $PID_FILE "" "$NAME"
}

case $1 in
        start)
                if status; then exit 0; fi
                start
                ;;
        stop)
                stop
                ;;
        reload)
                stop
                start
                ;;
        restart)
                stop
                start
                ;;
        status)
                status && exit 0 || exit $?
                ;;
        *)
                echo "Usage: $0 {start|stop|restart|reload|status}"
                exit 1
                ;;
esac

exit 0
