# coding: utf-8
from __future__ import print_function, unicode_literals

import calendar
import os
import re
import threading
import time
from operator import itemgetter

from .__init__ import ANYWIN, TYPE_CHECKING, unicode
from .bos import bos
from .up2k import up2k_wark_from_hashlist
from .util import (
    HAVE_SQLITE3,
    Daemon,
    Pebkac,
    absreal,
    gen_filekey,
    min_ex,
    quotep,
    s3dec,
)

if HAVE_SQLITE3:
    import sqlite3

try:
    from pathlib import Path
except:
    pass

if True:  # pylint: disable=using-constant-test
    from typing import Any, Optional, Union

if TYPE_CHECKING:
    from .httpsrv import HttpSrv


class U2idx(object):
    def __init__(self, hsrv: "HttpSrv") -> None:
        self.log_func = hsrv.log
        self.asrv = hsrv.asrv
        self.args = hsrv.args
        self.timeout = self.args.srch_time

        if not HAVE_SQLITE3:
            self.log("your python does not have sqlite3; searching will be disabled")
            return

        self.active_id = ""
        self.active_cur: Optional["sqlite3.Cursor"] = None
        self.cur: dict[str, "sqlite3.Cursor"] = {}
        self.mem_cur = sqlite3.connect(":memory:", check_same_thread=False).cursor()
        self.mem_cur.execute(r"create table a (b text)")

        self.p_end = 0.0
        self.p_dur = 0.0

    def log(self, msg: str, c: Union[int, str] = 0) -> None:
        self.log_func("u2idx", msg, c)

    def fsearch(
        self, vols: list[tuple[str, str, dict[str, Any]]], body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """search by up2k hashlist"""
        if not HAVE_SQLITE3:
            return []

        fsize = body["size"]
        fhash = body["hash"]
        wark = up2k_wark_from_hashlist(self.args.salt, fsize, fhash)

        uq = "substr(w,1,16) = ? and w = ?"
        uv: list[Union[str, int]] = [wark[:16], wark]

        try:
            return self.run_query(vols, uq, uv, True, False, 99999)[0]
        except:
            raise Pebkac(500, min_ex())

    def get_cur(self, ptop: str) -> Optional["sqlite3.Cursor"]:
        if not HAVE_SQLITE3:
            return None

        cur = self.cur.get(ptop)
        if cur:
            return cur

        histpath = self.asrv.vfs.histtab.get(ptop)
        if not histpath:
            self.log("no histpath for [{}]".format(ptop))
            return None

        db_path = os.path.join(histpath, "up2k.db")
        if not bos.path.exists(db_path):
            return None

        cur = None
        if ANYWIN and not bos.path.exists(db_path + "-wal"):
            uri = ""
            try:
                uri = "{}?mode=ro&nolock=1".format(Path(db_path).as_uri())
                db = sqlite3.connect(uri, 2, uri=True, check_same_thread=False)
                cur = db.cursor()
                cur.execute('pragma table_info("up")').fetchone()
                self.log("ro: {}".format(db_path))
            except:
                self.log("could not open read-only: {}\n{}".format(uri, min_ex()))
                # may not fail until the pragma so unset it
                cur = None

        if not cur:
            # on windows, this steals the write-lock from up2k.deferred_init --
            # seen on win 10.0.17763.2686, py 3.10.4, sqlite 3.37.2
            cur = sqlite3.connect(db_path, 2, check_same_thread=False).cursor()
            self.log("opened {}".format(db_path))

        self.cur[ptop] = cur
        return cur

    def search(
        self, vols: list[tuple[str, str, dict[str, Any]]], uq: str, lim: int
    ) -> tuple[list[dict[str, Any]], list[str], bool]:
        """search by query params"""
        if not HAVE_SQLITE3:
            return [], [], False

        q = ""
        v: Union[str, int] = ""
        va: list[Union[str, int]] = []
        have_up = False  # query has up.* operands
        have_mt = False
        is_key = True
        is_size = False
        is_date = False
        field_end = ""  # closing parenthesis or whatever
        kw_key = ["(", ")", "and ", "or ", "not "]
        kw_val = ["==", "=", "!=", ">", ">=", "<", "<=", "like "]
        ptn_mt = re.compile(r"^\.?[a-z_-]+$")
        ptn_lc = re.compile(r" (mt\.v) ([=<!>]+) \? \) $")
        ptn_lcv = re.compile(r"[a-zA-Z]")

        while True:
            uq = uq.strip()
            if not uq:
                break

            ok = False
            for kw in kw_key + kw_val:
                if uq.startswith(kw):
                    is_key = kw in kw_key
                    uq = uq[len(kw) :]
                    ok = True
                    q += kw
                    break

            if ok:
                continue

            if uq.startswith('"'):
                v, uq = uq[1:].split('"', 1)
                while v.endswith("\\"):
                    v2, uq = uq.split('"', 1)
                    v = v[:-1] + '"' + v2
                uq = uq.strip()
            else:
                v, uq = (uq + " ").split(" ", 1)
                v = v.replace('\\"', '"')

            if is_key:
                is_key = False

                if v == "size":
                    v = "up.sz"
                    is_size = True
                    have_up = True

                elif v == "date":
                    v = "up.mt"
                    is_date = True
                    have_up = True

                elif v == "path":
                    v = "trim(?||up.rd,'/')"
                    va.append("\nrd")
                    have_up = True

                elif v == "name":
                    v = "up.fn"
                    have_up = True

                elif v == "tags" or ptn_mt.match(v):
                    have_mt = True
                    field_end = ") "
                    if v == "tags":
                        vq = "mt.v"
                    else:
                        vq = "+mt.k = '{}' and mt.v".format(v)

                    v = "exists(select 1 from mt where mt.w = mtw and " + vq

                else:
                    raise Pebkac(400, "invalid key [{}]".format(v))

                q += v + " "
                continue

            head = ""
            tail = ""

            if is_date:
                is_date = False
                v = re.sub(r"[tzTZ, ]+", " ", v).strip()
                for fmt in [
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M",
                    "%Y-%m-%d %H",
                    "%Y-%m-%d",
                    "%Y-%m",
                    "%Y",
                ]:
                    try:
                        v = calendar.timegm(time.strptime(str(v), fmt))
                        break
                    except:
                        pass

            elif is_size:
                is_size = False
                v = int(float(v) * 1024 * 1024)

            else:
                if v.startswith("*"):
                    head = "'%'||"
                    v = v[1:]

                if v.endswith("*"):
                    tail = "||'%'"
                    v = v[:-1]

            q += " {}?{} ".format(head, tail)
            va.append(v)
            is_key = True

            if field_end:
                q += field_end
                field_end = ""

            # lowercase tag searches
            m = ptn_lc.search(q)
            zs = unicode(v)
            if not m or not ptn_lcv.search(zs):
                continue

            va.pop()
            va.append(zs.lower())
            q = q[: m.start()]

            field, oper = m.groups()
            if oper in ["=", "=="]:
                q += " {} like ? ) ".format(field)
            else:
                q += " lower({}) {} ? ) ".format(field, oper)

        try:
            return self.run_query(vols, q, va, have_up, have_mt, lim)
        except Exception as ex:
            raise Pebkac(500, repr(ex))

    def run_query(
        self,
        vols: list[tuple[str, str, dict[str, Any]]],
        uq: str,
        uv: list[Union[str, int]],
        have_up: bool,
        have_mt: bool,
        lim: int,
    ) -> tuple[list[dict[str, Any]], list[str], bool]:
        done_flag: list[bool] = []
        self.active_id = "{:.6f}_{}".format(
            time.time(), threading.current_thread().ident
        )
        Daemon(self.terminator, "u2idx-terminator", (self.active_id, done_flag))

        if not uq or not uv:
            uq = "select * from up"
            uv = []
        elif have_mt:
            uq = "select up.*, substr(up.w,1,16) mtw from up where " + uq
        else:
            uq = "select up.* from up where " + uq

        self.log("qs: {!r} {!r}".format(uq, uv))

        ret = []
        seen_rps: set[str] = set()
        lim = min(lim, int(self.args.srch_hits))
        taglist = {}
        for (vtop, ptop, flags) in vols:
            cur = self.get_cur(ptop)
            if not cur:
                continue

            self.active_cur = cur

            vuv = []
            for v in uv:
                if v == "\nrd":
                    v = vtop + "/"

                vuv.append(v)

            sret = []
            fk = flags.get("fk")
            dots = flags.get("dotsrch")
            c = cur.execute(uq, tuple(vuv))
            for hit in c:
                w, ts, sz, rd, fn, ip, at = hit[:7]

                if rd.startswith("//") or fn.startswith("//"):
                    rd, fn = s3dec(rd, fn)

                rp = quotep("/".join([x for x in [vtop, rd, fn] if x]))
                if not dots and "/." in ("/" + rp):
                    continue

                if rp in seen_rps:
                    continue

                if not fk:
                    suf = ""
                else:
                    try:
                        ap = absreal(os.path.join(ptop, rd, fn))
                        inf = bos.stat(ap)
                    except:
                        continue

                    suf = (
                        "?k="
                        + gen_filekey(
                            self.args.fk_salt, ap, sz, 0 if ANYWIN else inf.st_ino
                        )[:fk]
                    )

                lim -= 1
                if lim < 0:
                    break

                seen_rps.add(rp)
                sret.append({"ts": int(ts), "sz": sz, "rp": rp + suf, "w": w[:16]})

            for hit in sret:
                w = hit["w"]
                del hit["w"]
                tags = {}
                q2 = "select k, v from mt where w = ? and +k != 'x'"
                for k, v2 in cur.execute(q2, (w,)):
                    taglist[k] = True
                    tags[k] = v2

                hit["tags"] = tags

            ret.extend(sret)
            # print("[{}] {}".format(ptop, sret))

        done_flag.append(True)
        self.active_id = ""

        ret.sort(key=itemgetter("rp"))

        return ret, list(taglist.keys()), lim < 0

    def terminator(self, identifier: str, done_flag: list[bool]) -> None:
        for _ in range(self.timeout):
            time.sleep(1)
            if done_flag:
                return

        if identifier == self.active_id:
            assert self.active_cur
            self.active_cur.connection.interrupt()
