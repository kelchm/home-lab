ARG BASE=glm53-tensorfold:b463237b-20260930
FROM ${BASE}
COPY site_fixes.py /opt/glm53-tensorfold/site_fixes.py
COPY readiness.py /opt/glm53-tensorfold/readiness.py
RUN python3 /opt/glm53-tensorfold/site_fixes.py
LABEL org.opencontainers.image.source="https://github.com/kelchm/home-lab" \
      glm53.tensorfold.kit="b463237b014c1fd11915accc40ed6133691e5ea5"
