#!/usr/bin/env python3
"""Kiem tra tinh muc C9: worker pool batch cau hinh duoc 1-2 (mac dinh 2,
tu dong 1 khi RAM thiet bi <= 4 GB, luu localStorage) + sua cau sai ve
song song (pool chay song song THAT: moi worker 1 thread he dieu hanh
that; chi ben trong 1 worker thi Python tuan tu vi Pyodide khong co
pthreads). Manifest (files.json 101, tests.json 175) sau phan D; sw.js
CACHE_VERSION v39-20261117; khong them file site nao.
