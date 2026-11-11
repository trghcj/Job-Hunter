"""Tech skills found in resumes and job ads.

Each line is "Canonical | alias | alias". Matching ignores case and needs whole words, so
"Java" never matches inside "JavaScript" and "SQL" never inside "MySQL". Skills whose name is
also an everyday word (Go, R, C, Swift, Spring, Express, REST, AI) are listed only with
qualified forms ("golang", "swift (ios)", "spring boot", "rest api"), never the bare word.
"""

import re
from functools import lru_cache

_TABLE = """
Python | python3 | python 3
Java | core java | java 8 | java 11 | java 17
JavaScript | js | ecmascript | es6 | vanilla js
TypeScript | ts | typescript.js
C++ | cpp | c plus plus
C# | c sharp | csharp
C | c language | c programming | embedded c
Go | golang | go lang | go language
Rust | rust lang | rustlang
Kotlin | kotlin android
Swift | swift (ios) | swiftui | ios swift | swift programming | swift language
Objective-C | objective c | objc
Dart | dart language
Ruby | ruby lang
Ruby on Rails | rails | ror
PHP | php7 | php8
Perl
Scala
Haskell
Elixir
Erlang
Clojure
Lua
R | r programming | r language | rstudio
MATLAB
Julia Language | julia lang
Groovy
F# | fsharp
Visual Basic | vb.net | vba
COBOL
Fortran
Assembly | assembly language | x86 assembly
Solidity
Bash | bash scripting
Shell Scripting | shell script | shell scripts | unix shell
PowerShell
SQL | t-sql | ansi sql
PL/SQL | plsql
HTML | html5
CSS | css3
Sass | scss
Tailwind CSS | tailwind | tailwindcss
Bootstrap
Material UI | mui | material-ui
React | react.js | reactjs
React Native | react-native
Redux | redux toolkit
Next.js | nextjs | next js
Angular | angularjs | angular.js
Vue.js | vue | vuejs | vue js
Nuxt.js | nuxt | nuxtjs
Svelte | sveltekit
jQuery
Gatsby
Three.js | threejs
D3.js | d3
Webpack
Vite
Babel
Node.js | node | nodejs | node js
Express.js | expressjs | express.js framework
NestJS | nest.js
Deno
Bun.js | bun runtime
Django | django rest framework | drf
Flask
FastAPI
Celery
Spring Boot | springboot | spring framework | spring mvc | spring cloud
Hibernate
JPA
Maven
Gradle
.NET | .net core | dotnet | .net framework
ASP.NET | asp.net core | asp.net mvc
Entity Framework | ef core
Blazor
Laravel
Symfony
CodeIgniter
WordPress
Drupal
Magento
Shopify
GraphQL | apollo graphql
REST APIs | rest api | restful | restful api | restful apis | rest services
gRPC
WebSockets | websocket | socket.io
OAuth | oauth2 | oauth 2.0
JWT | json web token
SOAP
Microservices | microservice | micro services
System Design | high level design | low level design | hld | lld
Data Structures | data structure | dsa
Algorithms | algorithm
OOP | object oriented programming | oops | object-oriented
Design Patterns | design pattern
SOLID Principles
Multithreading | multi-threading | concurrency
Operating Systems | operating system
Computer Networks | computer networking
DBMS | database management
MySQL
PostgreSQL | postgres | postgresql database
SQLite
Oracle Database | oracle db | oracle sql
SQL Server | ms sql | mssql | microsoft sql server
MongoDB | mongo | mongo db
Redis
Cassandra
DynamoDB
Elasticsearch | elastic search | elk
Neo4j
CouchDB
MariaDB
Firebase | firestore
Supabase
Snowflake
BigQuery | big query
Redshift
NoSQL
Kafka | apache kafka
RabbitMQ
ActiveMQ
Amazon SQS | sqs
Docker | dockerfile | docker compose
Kubernetes | k8s | kubectl
Helm
OpenShift
Terraform
Ansible
Puppet
Chef Infra | chef infra
Jenkins
GitHub Actions
GitLab CI | gitlab ci/cd | gitlab
CircleCI
Travis CI
Argo CD | argocd
CI/CD | ci cd | continuous integration | continuous deployment
DevOps
SRE | site reliability engineering
AWS | amazon web services
AWS Lambda | lambda functions
EC2
S3 | amazon s3
CloudFormation
Azure | microsoft azure
Azure DevOps
GCP | google cloud | google cloud platform
Firebase Hosting
Heroku
Vercel
Netlify
DigitalOcean
Cloudflare
Linux | ubuntu | centos | redhat linux | rhel
Unix
Windows Server
Nginx
Apache HTTP Server | apache server | apache httpd
Tomcat
Prometheus
Grafana
Datadog
New Relic
Splunk
ELK Stack
Git | git version control
GitHub
Bitbucket
Jira
Confluence
Postman
Swagger | openapi
Agile | scrum | kanban
TDD | test driven development
BDD | behaviour driven development | behavior driven development
Unit Testing | unit tests
Integration Testing | integration tests
Manual Testing
Automation Testing | test automation | automated testing
API Testing
Performance Testing | load testing
Selenium | selenium webdriver
Cypress
Playwright
Appium
JUnit
TestNG
Mockito
pytest
Jest
Mocha
Cucumber
JMeter
LoadRunner
Machine Learning | ml | machine-learning
Deep Learning | dl
Artificial Intelligence | artificial intelligence/ml
Generative AI | genai | gen ai | generative ai/ml
Large Language Models | llm | llms | large language model
Prompt Engineering
RAG | retrieval augmented generation | retrieval-augmented generation
LangChain
LlamaIndex
Hugging Face | huggingface | transformers
OpenAI API | openai
NLP | natural language processing
Computer Vision | opencv
TensorFlow | tensor flow
PyTorch
Keras
scikit-learn | sklearn | scikit learn
XGBoost
Pandas
NumPy
SciPy
Matplotlib
Seaborn
Jupyter | jupyter notebook
Statistics | statistical analysis
Data Analysis | data analytics
Data Science
Data Engineering
Data Visualization | data visualisation
ETL | elt | etl pipelines
Apache Spark | spark | pyspark
Hadoop
Hive
Airflow | apache airflow
Databricks
dbt
Power BI | powerbi
Tableau
Looker
Excel | ms excel | microsoft excel | advanced excel
Google Sheets
MLOps
Feature Engineering
Time Series
Recommendation Systems | recommender systems
Reinforcement Learning
Android | android development | android sdk
iOS | ios development
Flutter
Xamarin
Ionic
Jetpack Compose
Mobile Development | mobile app development
Unity | unity3d
Unreal Engine | unreal
Game Development | game dev
Blockchain
Web3 | web 3.0
Ethereum
Smart Contracts | smart contract
Cybersecurity | cyber security | information security
Penetration Testing | pentesting | pen testing | ethical hacking
OWASP
SIEM
Network Security
Cryptography
Networking | tcp/ip | network protocols
DNS
Embedded Systems | embedded
Arduino
Raspberry Pi
IoT | internet of things
Verilog
VHDL
FPGA
RTOS
PCB Design
AutoCAD
SolidWorks
Salesforce | sfdc | apex
SAP | sap abap | sap hana
ServiceNow
Microsoft Dynamics | dynamics 365
UI/UX | ui ux | ux design | ui design
Figma
Adobe XD
Photoshop | adobe photoshop
Illustrator | adobe illustrator
Responsive Design | responsive web design
Accessibility | wcag | a11y
SEO
Web Development | web developer | full stack development
Frontend Development | front end development | front-end development
Backend Development | back end development | back-end development
Full Stack | fullstack | full-stack
MERN | mern stack
MEAN | mean stack
LAMP | lamp stack
JSON
XML
YAML
Regex | regular expressions
Linux Administration | linux admin | system administration
Virtualization | vmware
Serverless
Caching
Load Balancing | load balancer
Distributed Systems
Event-Driven Architecture | event driven architecture | event-driven
Domain-Driven Design | domain driven design | ddd
Clean Architecture
MVC | model view controller
MVVM
Spring Security
Keycloak
Auth0
Stripe
Razorpay
Twilio
Payment Gateway | payment gateways
Content Management | cms
Technical Writing
Communication Skills
Problem Solving | problem-solving
"""


def _parse() -> dict[str, tuple[str, ...]]:
    table: dict[str, tuple[str, ...]] = {}
    for line in _TABLE.strip().splitlines():
        names = [part.strip() for part in line.split("|") if part.strip()]
        canonical, aliases = names[0], names[1:]
        # A canonical name that is an everyday word is matched only through its aliases.
        own = [] if canonical in _QUALIFIED_ONLY else [canonical]
        table[canonical] = tuple(own + aliases)
    return table


_QUALIFIED_ONLY = {"Go", "R", "C", "Swift", "Julia Language", "Chef Infra", "Bun.js",
                   "Apache HTTP Server", "Firebase Hosting"}

SKILLS: dict[str, tuple[str, ...]] = _parse()


@lru_cache(maxsize=1)
def _matcher() -> tuple[re.Pattern[str], dict[str, str]]:
    lookup = {alias.lower(): canonical for canonical, names in SKILLS.items() for alias in names}
    alternation = "|".join(re.escape(a) for a in sorted(lookup, key=len, reverse=True))
    # Whole words only: an alias may not touch a letter, digit, "+", "#" or "." on its left,
    # nor a letter, digit, "+" or "#" on its right (a full stop after it ends a sentence).
    pattern = re.compile(rf"(?<![\w+#.])(?:{alternation})(?![\w+#])(?!\.\w)")
    return pattern, lookup


def extract_skills(text: str | None) -> set[str]:
    """Canonical names of the skills mentioned in `text`."""
    if not text:
        return set()
    pattern, lookup = _matcher()
    return {lookup[m.group(0)] for m in pattern.finditer(text.lower())}
